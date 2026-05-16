# ADR-003: Pluggable Identity Store and Config Hot-Reload

**Date:** 2026-05-15
**Status:** Accepted
**Deciders:** Platform team

---

## Context

Through v0.3.8 the application loaded its entire identity store — users, service
principals, client apps, auth mode, CORS settings — from a single YAML file at
startup. Two pressures drove this ADR:

1. **Persistence** — a ConfigMap is wiped on pod restart. Teams that manage
   identities through an API (rather than editing YAML) need the data to survive
   restarts. A Postgres backend is the most likely target; others (Redis, SQLite,
   Vault dynamic secrets) may follow.

2. **Zero-restart config reload** — Kubernetes automatically remounts ConfigMap
   data into the pod's filesystem within the kubelet sync window (~1 min). The
   application had no way to pick up the new file without a pod restart, which
   disrupts in-flight tests and adds operational friction.

---

## Decision

### 1. IdentityStore protocol

All backend-specific logic is encapsulated behind an `IdentityStore` protocol
(Python structural typing — no base class required). The interface is defined in
`src/mock_idp/store/__init__.py`.

```python
class IdentityStore(Protocol):
    @property
    def mode(self) -> str: ...
    @property
    def issuer_modes(self) -> dict[str, str]: ...
    @property
    def admin_token(self) -> str: ...
    @property
    def cors_origins(self) -> list[str]: ...
    @property
    def users(self) -> dict[str, UserRecord]: ...
    @property
    def service_principals(self) -> dict[str, ServicePrincipalRecord]: ...
    @property
    def service_principals_raw(self) -> dict[str, ServicePrincipalRecord]: ...
    @property
    def client_apps(self) -> dict[str, ClientAppRecord]: ...
    def reload(self) -> None: ...
```

**Key constraint on dict properties:** implementations must return the *same*
dict object across calls. Callers in `config.py` hold references to those dicts;
backends keep them live by updating in-place (`dict.clear()` + `dict.update()`)
rather than replacing them. This keeps the module-level aliases in `config.py`
correct without any caller changes.

### 2. YamlIdentityStore (default backend)

`src/mock_idp/store/yaml_store.py` — the YAML-file-backed reference
implementation. Behaviour:

- **Startup failure:** if the config file fails Pydantic validation the process
  exits with a formatted error message (`sys.exit(1)`). A broken ConfigMap
  should prevent startup, not produce silently wrong tokens.
- **Reload safety:** if a hot-reload produces an invalid config the error is
  logged and the *previous* state is preserved. The server continues serving
  valid tokens from the last good config.
- **Pre-validation linting:** before Pydantic validation runs, a structural
  lint pass logs actionable warnings for common YAML mistakes (numeric
  passwords, service principals nested under `users:`, key typos).

### 3. Config hot-reload

`main.py` starts a background `asyncio` task that watches the config file using
`watchfiles` (OS-level inotify/FSEvents/kqueue — no polling). When a change is
detected:

```
file changes → awatch() fires → _cfg.reload_config()
  → store.reload()        (re-parses YAML, validates, applies in-place)
  → MODE = store.mode     (re-bind scalars at the module level)
  → ADMIN_TOKEN = …
  → CORS_ORIGINS = …
```

**Kubernetes ConfigMap flow:**

```
kubectl apply -f configmap.yaml
  → kubelet detects change (≤ kubelet sync period, default 60 s)
  → kubelet rewrites the mounted file in the pod
  → inotify fires in the container
  → mock-idp reloads within ~1 s of file write
```

No pod restart required. The signing keys, active connections, and the FastAPI
middleware stack are unaffected.

**CORS limitation:** `CORSMiddleware` captures `cors_allow_origins` at startup.
Changes to that list take effect only after a pod restart. All other config
values (`auth_mode`, `admin_token`, identities, grants) reload immediately.

### 4. Factory and backend selection

`store.create_store(backend, **kwargs)` is the single entry point. `config.py`
calls it once:

```python
store: IdentityStore = create_store("yaml", path=CONFIG_PATH)
```

To switch backends, change this one line. No router, token helper, or test
fixture needs to know which backend is active.

### 5. Module layout

```
src/mock_idp/
  store/
    __init__.py      IdentityStore protocol + create_store() factory
    yaml_store.py    YamlIdentityStore (YAML file backend)
    # future:
    # pg_store.py    PostgresIdentityStore
  config.py          Thin wrapper: creates store, exposes module-level aliases
  main.py            FastAPI app + lifespan (file watcher background task)
```

---

## Adding a new backend

1. Create `src/mock_idp/store/pg_store.py` (or similar).
2. Implement all properties and `reload()`. Dict properties must update in-place.
3. Register it in `create_store()`:
   ```python
   if backend == "postgres":
       from .pg_store import PostgresIdentityStore
       return PostgresIdentityStore(dsn=kwargs["dsn"])
   ```
4. Add the driver (`asyncpg`, `psycopg[async]`, etc.) to `pyproject.toml`.
5. Decide how the DSN is supplied — environment variable is the most
   Kubernetes-native approach:
   ```bash
   MOCK_IDP_BACKEND=postgres
   MOCK_IDP_PG_DSN=postgresql+asyncpg://user:pass@postgres/mock_idp
   ```
6. Update `config.py` to read these env vars and pass them to `create_store`.

### PostgresIdentityStore sketch

```python
class PostgresIdentityStore:
    """Postgres-backed store. Not yet implemented."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._mode = "lax"
        self._issuer_modes: dict[str, str] = {}
        self._admin_token = "change-me"
        self._cors_origins: list[str] = ["*"]
        self._users: dict[str, UserRecord] = {}
        self._service_principals: dict[str, ServicePrincipalRecord] = {}
        self._service_principals_raw: dict[str, ServicePrincipalRecord] = {}
        self._client_apps: dict[str, ClientAppRecord] = {}
        self._load_sync()  # blocking at startup; use asyncpg.create_pool in lifespan

    # ... implement @property accessors and reload() ...

    def reload(self) -> None:
        """Re-query Postgres and update dicts in-place."""
        # Implementation: SELECT from identities, service_principals, client_apps
        # tables; diff against current state; apply changes in-place.
        ...
```

**Schema considerations for Postgres:**

| Table | Key columns |
|---|---|
| `tenants` | `tid`, `provider`, `auth_mode` |
| `users` | `tid`, `username`, `password_hash`, `oid`, `upn`, `token_version`, … |
| `service_principals` | `tid`, `name`, `client_id`, `secret_hash`, `token_version`, … |
| `client_apps` | `tid`, `audience_uri`, `app_id`, `label` |
| `grants` | `tid`, `audience_uri`, `identity_name`, `roles[]` |

Secrets should be hashed (bcrypt/argon2) or fetched from Vault at query time
rather than stored as plaintext — matching the spirit of the secret-management
roadmap item.

---

## Alternatives considered

### A. Keep the YAML file as the only backend; add a REST admin API for writes

The admin API (e.g. `POST /admin/users`) writes back to the YAML file. The file
watcher then reloads it.

**Rejected.** Writing to a file inside a Kubernetes pod is fragile (writable
mounts, conflict with ConfigMap management). Adds a new write path that must be
kept consistent with the YAML format.

### B. SQLite as the "portable" persistence option

SQLite is available without a separate process. Easy to get started.

**Not rejected outright, but not implemented.** SQLite's concurrency model is
fine for a test fixture; it doesn't add operational complexity. If Postgres feels
heavy for a team, SQLite is a good intermediate step. The protocol supports it;
add `sqlite_store.py` when a concrete demand exists.

### C. Structural typing (Protocol) vs. abstract base class (ABC)

ABC would enforce method presence at class definition time. Protocol enforces it
at type-check time (mypy/pyright) via structural compatibility.

**Protocol chosen** for lighter coupling. No backend needs to import from the
core package to satisfy the interface. This is especially important for backends
that might live in separate packages or be contributed externally.

### D. Async store interface

Make all properties and `reload()` async so a Postgres backend can `await`
queries directly in the router.

**Deferred.** It would require routers to `await store.get_user(key)` everywhere
they currently do a dict lookup. The in-place dict model means reads are
synchronous O(1) hash lookups; only `reload()` is I/O-bound. For the YAML
backend `reload()` is fast enough to run synchronously without blocking the event
loop. A Postgres backend's `reload()` should fetch the data in a thread pool
(`asyncio.to_thread`) or via an async init path in the lifespan, keeping the
protocol synchronous while the implementation is non-blocking.

---

## Consequences

**Good:**

- Any backend can drop in behind a single changed line in `config.py`.
- Zero-restart ConfigMap hot-reload works out of the box on Kubernetes.
- Reload failures are safe: the server keeps serving valid tokens from the last
  good config.
- The protocol is a clear contract for future contributors building new backends.
- Pre-validation linting and Pydantic error formatting (v0.3.8) are preserved
  inside `YamlIdentityStore` — they belong to the YAML loading concern, not the
  application core.

**Trade-offs:**

- The in-place dict update contract is implicit — a backend that replaces dicts
  instead of updating them will silently break `config.py`'s module-level
  aliases. This is documented in the protocol's docstring; a future improvement
  would add a runtime assertion in `create_store`.
- CORS origins are still not hot-reloadable (middleware limitation). Documented
  in `config.py`.
- `reload()` is synchronous. A Postgres backend must use `asyncio.to_thread` or
  a pre-fetched async pool to avoid blocking the event loop.

---

## Related

- [`ADR-001`](ADR-001-python-mock-oidc.md) — original build decision
- [`ADR-002`](ADR-002-provider-plugin-architecture.md) — provider plugin architecture
- [`roadmap.md`](roadmap.md) — hot-reload and modular backend moved to Resolved
- [`docs/architecture.md`](docs/architecture.md) — endpoint and flow diagrams
