# mock-idp

FastAPI mock identity provider that emits configurable OIDC-compliant JWTs for
testing API gateway authentication. Supports `password` and `client_credentials`
grants, per-identity token shape (v1/v2), lax/strict audience gating, admin
overrides, key rotation, CORS, and a browser token playground.

Full architecture, design decisions, and test scenario coverage live in
[`docs/mock-oidc/`](docs/mock-oidc/). Running into a problem? See
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

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
3. **Build & push** — image pushed to `ghcr.io/your-org/mock-idp`
4. **Scan** — Trivy scans for CRITICAL/HIGH CVEs (blocks on findings)

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
src/
  app.py            FastAPI application (~330 LOC)
  playground.html   Token playground served at GET /
tests/
  test_app.py       pytest suite
chart/              Helm chart
manifests/
  mock-idp.yaml     Raw K8s manifests (reference only)
.github/workflows/
  ci.yml            Lint → test → build/push → Trivy scan
.pre-commit-config.yaml  Pre-commit hooks
config.example.yaml Sample identity store for local dev
Dockerfile
pyproject.toml      Project metadata and dependencies
uv.lock             Locked dependency versions
docs/mock-oidc/     Architecture, ADR, roadmap, test scenarios, briefs
```
