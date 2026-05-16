# mock-idp

[![CI](https://github.com/TheGrandWazoo/mock-idp/actions/workflows/ci.yml/badge.svg)](https://github.com/TheGrandWazoo/mock-idp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/TheGrandWazoo/mock-idp)](https://github.com/TheGrandWazoo/mock-idp/releases)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Docker](https://img.shields.io/badge/ghcr.io-mock--idp-blue?logo=docker&logoColor=white)](https://github.com/TheGrandWazoo/mock-idp/pkgs/container/mock-idp)
[![License: ELv2](https://img.shields.io/badge/license-ELv2-orange)](LICENSE)

FastAPI mock identity provider that emits configurable OIDC-compliant JWTs for
testing API gateway authentication. Supports `password`, `client_credentials`, and
`token-exchange` (RFC 8693) grants, token introspection (RFC 7662), per-identity
token shape (v1/v2), per-issuer signing keys, lax/strict audience gating, admin
overrides, key rotation, CORS, and a browser token playground.

Full architecture, design decisions, test scenarios, and troubleshooting live in
[`docs/mock-oidc/`](docs/mock-oidc/).

---

## Local development

```bash
uv sync
uv tool install pre-commit
pre-commit install

export CONFIG_PATH="config.example.yaml"
export ISS_BASE="http://localhost:8080"

uv run uvicorn mock_idp.main:app --reload --port 8080
```

Open `http://localhost:8080/` for the token playground.

## Run tests

```bash
uv run pytest tests -v
```

## Build image locally

```bash
docker build -t mock-idp:local .
docker run --rm -p 8080:8080 \
  -v "$PWD/config.example.yaml:/etc/mock-idp/config.yaml:ro" \
  mock-idp:local
```

## CI / GitHub Actions

On every push to `main`:

1. **Lint** — `ruff check src tests`
2. **Test** — `pytest tests`
3. **Build & push** — image pushed to `ghcr.io/thegrandwazoo/mock-idp`
4. **Scan** — Trivy scans for CRITICAL/HIGH CVEs; results uploaded to GitHub Security tab

`git tag v*` also builds a versioned image + `latest`.

PRs run lint + test only (no push).

## Deploy to Kubernetes

```bash
helm upgrade --install mock-idp ./chart \
  -n mock-idp --create-namespace \
  --set ingress.host=mock-idp.example.com \
  --set image.tag=sha-abc1234
```

ConfigMap changes trigger an automatic pod restart via the `checksum/config` annotation.

## License

[Elastic License 2.0](LICENSE) — free for internal and development use; hosting
as a managed service for third parties requires a commercial license.

## Project layout

```
src/mock_idp/
  main.py           FastAPI app entrypoint; lifespan wires store startup/shutdown
  config.py         Config loader; exports USERS, SERVICE_PRINCIPALS, CLIENT_APPS
  models.py         Pydantic models (UserRecord, ServicePrincipalRecord, …)
  tokens.py         Claim helpers: resolve_roles, check_audience, sign, verify_token, …
  keys.py           Per-issuer RSA key stores; lazy creation; rotate()
  providers/
    __init__.py     Provider registry: get_provider(name) → module
    entra_id.py     Entra ID claim building: user_claims(), sp_claims()
  store/
    __init__.py     IdentityStore protocol + create_store() factory
    yaml_store.py   YAML file backend (default)
    pg_store.py     Postgres backend (MOCK_IDP_BACKEND=postgres)
  routers/
    oidc.py         Discovery, JWKS, /token (password/cc/exchange), /introspect, /userinfo
    admin.py        POST /admin/rotate-jwks[?issuer=], POST /admin/reload-config
    debug.py        /debug/identities, /debug/config, /debug/decode
    playground.py   Serves playground.html at GET /
src/playground.html Browser token playground
tests/
  test_app.py       pytest suite (66 tests)
alembic/            Postgres schema migrations
chart/              Helm chart
manifests/
  mock-idp.yaml     Raw K8s manifests (reference only)
.github/
  workflows/ci.yml  Lint → test → build/push → Trivy scan + SARIF upload
  dependabot.yml    Weekly pip + Actions updates
.pre-commit-config.yaml  Pre-commit hooks (ruff, yaml, helm)
config.example.yaml Sample identity store for local dev
Dockerfile
pyproject.toml      Project metadata and dependencies
uv.lock             Locked dependency versions
docs/mock-oidc/     ADRs, roadmap, architecture, test scenarios, troubleshooting
```
