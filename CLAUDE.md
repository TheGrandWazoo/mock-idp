# mock-idp — Claude context

## What this is

FastAPI mock identity provider that emits configurable OIDC-compliant JWTs for testing API gateway authentication. Supports `password` and `client_credentials` grants, per-identity token shape (v1/v2), lax/strict audience gating, admin overrides, key rotation, CORS, and a browser token playground.

## Environment

- **Shell:** WSL (Linux). Always use bash/Linux syntax — `export VAR=value`, `source .venv/bin/activate`, forward slashes. Never PowerShell.

## Package manager: uv

This project uses **uv**, not pip. Key commands:

```bash
uv sync              # install all deps (including dev)
uv sync --no-dev     # prod deps only
uv run <cmd>         # run a command in the venv
uv lock              # regenerate uv.lock after editing pyproject.toml
```

Dependencies live in `pyproject.toml`. `uv.lock` pins exact versions. Do not reference `requirements.txt` — it no longer exists.

## Project layout

```
src/
  app.py            FastAPI application
  playground.html   Token playground served at GET /
tests/
  test_app.py       pytest suite
manifests/
  mock-idp.yaml     ConfigMap + Deployment + Service + Ingress
.github/workflows/
  ci.yml            Lint → test → build/push → Trivy scan
config.example.yaml Sample identity store for local dev
pyproject.toml      Project metadata and dependencies
uv.lock             Locked dependency versions
Dockerfile
docs/mock-oidc/     Architecture, ADR, roadmap, test scenarios, briefs
```

## Running locally

```bash
uv sync
uv tool install pre-commit
pre-commit install

export CONFIG_PATH="config.example.yaml"
export ISS_BASE="http://localhost:8080"

uv run uvicorn mock_idp.main:app --reload --port 8080
```

## Pre-commit hooks

Configured in `.pre-commit-config.yaml`. Runs on every commit:
- trailing whitespace, end-of-file newline, LF line endings
- YAML validation, merge conflict detection, private key detection
- `ruff` lint + format (Python)
- `helm-docs` (regenerates `chart/README.md` from annotated `values.yaml`)
- `helm lint` (on any `chart/` change)

## Running tests

```bash
uv run pytest tests -v
```
