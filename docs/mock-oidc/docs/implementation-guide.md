# Implementation Guide — Python Mock OIDC

How the server is structured, how to run it locally, and reference for
Dockerfile and Kubernetes manifests.

---

## Project layout

```text
mock-idp/
├── src/
│   ├── mock_idp/
│   │   ├── __init__.py
│   │   ├── main.py          # FastAPI app factory; wires routers + CORS
│   │   ├── config.py        # Config loading, module-level state
│   │   ├── keys.py          # RSA signing key management and rotation
│   │   ├── models.py        # Pydantic models (UserRecord, ClientRecord, AppConfig)
│   │   ├── tokens.py        # Claim building, shape resolution, audience check
│   │   └── routers/
│   │       ├── oidc.py      # OIDC core: discovery, JWKS, token, userinfo
│   │       ├── debug.py     # /debug/decode, /debug/identities, /debug/config
│   │       ├── admin.py     # /admin/rotate-jwks
│   │       └── playground.py # GET / (HTML token playground)
│   └── playground.html      # Token playground page (served at GET /)
├── tests/
│   └── test_app.py          # pytest suite (~31 scenarios)
├── chart/                   # Helm chart
├── manifests/
│   └── mock-idp.yaml        # Raw K8s Deployment + Service + Ingress
├── .github/workflows/
│   └── ci.yml               # Lint → test → build/push → Trivy scan
├── .pre-commit-config.yaml
├── config.example.yaml      # Sample identity store for local dev
├── Dockerfile
└── pyproject.toml
```

---

## pyproject.toml

```toml
[project]
name = "mock-idp"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "fastapi==0.136.1",
    "uvicorn[standard]==0.46.0",
    "authlib==1.7.2",
    "python-multipart==0.0.28",
    "pyyaml==6.0.3",
    "pydantic==2.13.4",
]

[dependency-groups]
dev = [
    "ruff==0.15.12",
    "pytest==9.0.3",
    "httpx==0.28.1",
]

[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.ruff]
target-version = "py314"
```

Versions are pinned for reproducibility. Run `uv lock` to regenerate
`uv.lock` after bumps. Bump on a quarterly cadence.

---

## config.example.yaml

The identity store. Loaded once at startup; restart the pod to pick up
changes.

```yaml
auth_mode: lax
cors_allow_origins: ["*"]
admin_token: change-me-in-real-deployments

users:
  alice:
    password: alice-pw
    upn: alice@example.com
    preferred_username: alice@example.com
    oid: 11111111-1111-1111-1111-aaaaaaaaaaaa
    tid: 22222222-2222-2222-2222-222222222222
    token_version: v2
    token_lifetime_seconds: 300
    roles: [operator, responder]
    groups: [platform-team]
    allowed_audiences:
      - api://serviceB
      - api://serviceC
    extra_claims:
      department: engineering
      cost_center: cc-1234

  bob:
    password: bob-pw
    upn: bob@example.com
    preferred_username: bob@example.com
    oid: 11111111-1111-1111-1111-bbbbbbbbbbbb
    token_version: v2
    token_lifetime_seconds: 300
    roles: [metrics-reader]
    groups: [analytics-team]
    allowed_audiences: [api://serviceB]

clients:
  service-a:
    client_id: 01010101-1010-1010-1010-aaaaaaaaaaaa
    secret: serviceA-secret
    label: ServiceA
    token_version: v1
    token_lifetime_seconds: 3600
    roles: [m2m]
    groups: [service-accounts]
    allowed_audiences: [api://serviceB]
    extra_claims:
      tier: 1

  service-b:
    client_id: 02020202-2020-2020-2020-bbbbbbbbbbbb
    secret: serviceB-secret
    label: ServiceB
    token_version: v1
    roles: [m2m]
    groups: [service-accounts]
    allowed_audiences: [api://serviceC]

  "00000000-0000-0000-0000-000000000000":
    secret: admin-secret
    label: TestAdmin
    override_any_claim: true
```

---

## Module walkthrough

### `config.py`

Loads the YAML config at import time and exposes module-level state:
`MODE`, `ADMIN_TOKEN`, `CORS_ORIGINS`, `USERS`, `CLIENTS`. Other
modules import `config` as a module (not its names directly) so runtime
mutations (e.g., strict-mode toggle in tests) are visible everywhere.

### `models.py`

Pydantic v2 models: `UserRecord`, `ClientRecord`, `AppConfig`, and
`DecodeRequest`. Field defaults match the config schema above.

### `keys.py`

Manages per-issuer RSA-2048 key stores. The `_IssuerKeys` class holds a
signing key, an unpublished alt key, and two decoy keys for each issuer.
Key stores are created lazily on first use and held in the module-level
`_stores` dict (protected by a `threading.Lock` for creation only).

Public API:
- `get_signing_key(issuer)` / `get_alt_key(issuer)` / `get_jwks_keys(issuer)` /
  `get_signing_public_key_pem(issuer)` — per-issuer accessors.
- `rotate(issuer=None)` — rotate one issuer or all known issuers; returns the
  new key (single issuer) or a `{issuer: kid}` dict (all issuers).
- `all_signing_kids()` — dict of `{issuer: signing_kid}` for all known issuers;
  used by `/debug/config`.
- `all_jwks_keys()` — flat list of all published keys across all known issuers;
  used by `/debug/decode`.

### `tokens.py`

All claim-building logic: `resolve_aud`, `resolve_shape`, `resolve_expiry`,
`check_audience`, `user_claims`, `client_claims`, `apply_overrides`,
`omit`, `sign`. Imports `config` as a module so `check_audience` reads
`_cfg.MODE` dynamically.

### `routers/oidc.py`

OIDC endpoints: `/healthz`, `/{issuer}/.well-known/openid-configuration`,
`/{issuer}/jwks`, `POST /{issuer}/token` (password / client_credentials /
token-exchange grants), `POST /{issuer}/introspect` (RFC 7662),
`GET /{issuer}/userinfo`, `POST /{issuer}/token/wrong-sig`,
`POST /{issuer}/token/unsigned`, `POST /{issuer}/token/wrong-alg`,
`GET /{issuer}/token/malformed`. Every key call passes `issuer` from the
route parameter so each endpoint uses that issuer's own keypair.

### `routers/debug.py`

`/debug/decode` — decodes any JWT and validates its signature against
`all_jwks_keys()` (all known issuers' published keys).
`/debug/identities` — loaded identity store with secrets redacted.
`/debug/config` — runtime config including `signing_kids` dict
(`{issuer: kid}` for all known issuers).

### `routers/admin.py`

`POST /admin/rotate-jwks[?issuer=<slug>]` — delegates to `keys.rotate(issuer)`;
rotates one issuer or all if `issuer` is omitted.
`POST /admin/reload-config` — triggers an in-place reload of the identity store.

### `routers/playground.py`

`GET /` — serves `src/playground.html` (locally) or `/app/playground.html`
(in Docker) as an `HTMLResponse`.

### `main.py`

App factory: wires CORS middleware and includes all four routers.

---

## Dockerfile

```dockerfile
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/mock_idp ./mock_idp
COPY src/playground.html ./

EXPOSE 8080

ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "mock_idp.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Build and push:

```bash
docker build -t ghcr.io/your-org/mock-idp:0.2.0 .
docker push   ghcr.io/your-org/mock-idp:0.2.0
```

---

## Kubernetes manifests

### Helm (recommended)

```bash
helm upgrade --install mock-idp ./chart \
  -n mock-idp --create-namespace \
  --set ingress.host=mock-idp.example.com \
  --set image.tag=sha-abc1234
```

ConfigMap changes trigger an automatic pod restart via the
`checksum/config` annotation.

### Raw manifest — `manifests/mock-idp.yaml`

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: mock-idp-config
  namespace: mock-idp
data:
  config.yaml: |
    auth_mode: lax
    cors_allow_origins: ["*"]
    admin_token: change-me-in-real-deployments
    users:
      alice:
        password: alice-pw
        upn: alice@example.com
        preferred_username: alice@example.com
        oid: 11111111-1111-1111-1111-aaaaaaaaaaaa
        token_version: v2
        token_lifetime_seconds: 300
        roles: [operator, responder]
        groups: [platform-team]
        allowed_audiences: [api://serviceB, api://serviceC]
        extra_claims:
          department: engineering
    clients:
      service-a:
        client_id: 01010101-1010-1010-1010-aaaaaaaaaaaa
        secret: serviceA-secret
        label: ServiceA
        token_version: v1
        roles: [m2m]
        groups: [service-accounts]
        allowed_audiences: [api://serviceB]
      "00000000-0000-0000-0000-000000000000":
        secret: admin-secret
        label: TestAdmin
        override_any_claim: true
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-idp
  namespace: mock-idp
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: mock-idp
  template:
    metadata:
      labels:
        app.kubernetes.io/name: mock-idp
        app.kubernetes.io/component: oidc-mock
    spec:
      containers:
        - name: mock-idp
          image: ghcr.io/your-org/mock-idp:latest
          ports:
            - { name: http, containerPort: 8080 }
          env:
            - { name: CONFIG_PATH, value: /etc/mock-idp/config.yaml }
          volumeMounts:
            - { name: config, mountPath: /etc/mock-idp, readOnly: true }
          readinessProbe:
            httpGet: { path: /healthz, port: http }
          livenessProbe:
            httpGet: { path: /healthz, port: http }
            initialDelaySeconds: 10
            periodSeconds: 30
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits:   { cpu: 200m, memory: 128Mi }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 65532
            capabilities: { drop: ["ALL"] }
      volumes:
        - name: config
          configMap: { name: mock-idp-config }
---
apiVersion: v1
kind: Service
metadata:
  name: mock-idp
  namespace: mock-idp
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: mock-idp
  ports:
    - { name: http, port: 8080, targetPort: http }
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mock-idp
  namespace: mock-idp
spec:
  ingressClassName: nginx   # substitute your cluster's ingress class
  tls:
    - secretName: tls-mock-idp
      hosts: [mock-idp.example.com]
  rules:
    - host: mock-idp.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: mock-idp
                port: { name: http }
```

---

## Local development

```bash
uv sync

export CONFIG_PATH=config.example.yaml
export ISS_BASE=http://localhost:8080

uv run uvicorn mock_idp.main:app --reload --port 8080
```

Open `http://localhost:8080/` for the token playground.

### Sample requests

**User flow:**

```bash
# Happy path
curl -X POST http://localhost:8080/default/token \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB"

# Force v1 shape
curl -X POST http://localhost:8080/default/token \
  -H "X-Token-Shape: v1" \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB"
```

**M2M (using mnemonic alias):**

```bash
curl -X POST http://localhost:8080/default/token \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"

# Equivalent — using the canonical UUID directly
curl -X POST http://localhost:8080/default/token \
  -d "grant_type=client_credentials" \
  -d "client_id=01010101-1010-1010-1010-aaaaaaaaaaaa" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"
```

**Strict-mode rejection (with `auth_mode: strict`):**

```bash
curl -X POST http://localhost:8080/default/token \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceZ"
# 400 invalid_target
```

**Admin override:**

```bash
curl -X POST http://localhost:8080/default/token \
  -d "grant_type=client_credentials" \
  -d "client_id=00000000-0000-0000-0000-000000000000" \
  -d "client_secret=admin-secret" \
  -d "resource=api://wherever" \
  -d "roles=admin,superuser" \
  -d "oid=custom-oid"
```

**Debug endpoints:**

```bash
# Decode any JWT
curl -X POST http://localhost:8080/debug/decode \
  -H "Content-Type: application/json" \
  -d '{"token": "eyJ..."}'

# See what's loaded
curl http://localhost:8080/debug/identities | jq .

# Effective runtime state
curl http://localhost:8080/debug/config | jq .
```

**Admin key rotation:**

```bash
curl -X POST http://localhost:8080/admin/rotate-jwks \
  -H "X-Admin-Token: change-me-in-real-deployments"
```

**Negative-case fixtures:**

```bash
curl -X POST http://localhost:8080/default/token/wrong-sig \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"

curl http://localhost:8080/default/token/malformed
```

---

## Testing

```bash
uv run pytest tests -v
```

The suite covers all surface areas in `test-scenarios.md` via FastAPI's
synchronous `TestClient`. All tests are synchronous — `TestClient` spins
up the ASGI app internally, so no async test infrastructure is required.

Groups: grants (happy + auth failures), token shape (config/header/suffix),
audience (resource/scope/both/neither), strict mode, admin override,
extra claims, override headers, negative endpoints, multi-issuer, debug
endpoints, admin rotate-jwks.

---

## End-to-end with an API gateway

Point the gateway's OIDC plugin issuer config at the in-cluster Service
DNS:

```text
http://mock-idp.mock-idp.svc.cluster.local:8080/default/.well-known/openid-configuration
```

Test clients request tokens from the ingress hostname
(`https://mock-idp.example.com/default/token` or the playground at
`https://mock-idp.example.com/`), then present the JWT to the gateway
route under test.

---

## Troubleshooting

### Tests fail with `ImportError` or `AttributeError` on startup

Run `uv sync` to ensure all dependencies are installed. If you added a
dependency, run `uv lock` first then `uv sync`.

### `uv run pytest` passes locally but CI fails

Check that `CONFIG_PATH` and `ISS_BASE` env vars are set. In CI these are
injected by the workflow; locally you need:

```bash
export CONFIG_PATH=config.example.yaml
export ISS_BASE=http://localhost:8080
```

### Ruff lint fails on a file I didn't touch

Run `uv run ruff check src tests` locally before pushing. Pre-commit also
runs ruff — install hooks with `uv tool install pre-commit && pre-commit install`.

### The config file changes aren't being picked up in tests

The test suite uses a module-scoped `TestClient` fixture. Config state is
shared across tests in the same module run. If you need a clean config for a
specific test, reload via `_cfg.reload_config()` in the test setup or run
that test in isolation with `pytest tests -k test_name`.

### `keys.py` generates new keypairs on every test run

Correct — keys are generated at process startup. Each `pytest` invocation is a
fresh process. Tests that check key kids (e.g. `test_jwks_active_kid_matches_token`)
are self-contained: they fetch the kid from the JWKS in the same test, not from
a hardcoded expected value.

### Adding a new endpoint — checklist

1. Add the handler in the appropriate router (`routers/`).
2. If the endpoint uses per-issuer keys, pass `issuer` from the route param to
   `get_signing_key(issuer)` / `get_jwks_keys(issuer)` etc.
3. Add at least one happy-path and one failure test in `tests/test_app.py`.
4. Update `test-scenarios.md` with a new scenario entry and troubleshooting callout.
5. Update `architecture.md` endpoint table.
6. If it's a roadmap item, update `roadmap.md` Resolved section and write an
   ADR if the decision is non-obvious.
