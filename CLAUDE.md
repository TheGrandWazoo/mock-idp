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
src/mock_idp/
  main.py           FastAPI app entrypoint
  config.py         Config loader; exports USERS, SERVICE_PRINCIPALS, CLIENT_APPS
  models.py         Pydantic models (UserRecord, ServicePrincipalRecord, ClientAppRecord, TenantRecord, AppConfig)
  tokens.py         Provider-agnostic helpers: resolve_roles, check_audience, sign, redact, …
  keys.py           RSA key generation and rotation
  providers/
    __init__.py     Provider registry: get_provider(name) → module
    entra_id.py     Entra ID claim building: user_claims(), sp_claims()
  routers/
    oidc.py         Discovery, JWKS, /token, /userinfo endpoints
    admin.py        POST /admin/rotate-jwks
    debug.py        /debug/identities, /debug/config, /debug/decode
    playground.py   Serves playground.html at GET /
src/playground.html Browser token playground
tests/
  test_app.py       pytest suite (35 tests)
manifests/
  mock-idp.yaml     ConfigMap + Deployment + Service + Ingress
.github/workflows/
  ci.yml            Lint → test → build/push → Trivy scan
config.example.yaml Sample identity store (v0.3 schema with tenants + grants)
pyproject.toml      Project metadata and dependencies
uv.lock             Locked dependency versions
Dockerfile
docs/mock-oidc/     ADRs, roadmap, architecture, test scenarios, briefs
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

---

## Agent / Minion Rules

- **Minions are research-only.** They search, read, and web-fetch. They return findings to main Claude. They never write files, commit, push, or delete.
- **Minions do not talk to each other.** All findings route through main Claude.
- **Main Claude does all writing, committing, and pushing.**

## File Rules

- **New files:** write freely.
- **Changes to existing files:** targeted edits only — add or modify, never blank out content.
- **Total replacement of an existing file:** write the replacement as a NEW file first, explain to the user why the old file needs replacing, wait for explicit approval. Never silently overwrite.
- **Briefs and reports:** always new dated files. Never overwrite a previous brief. The historical record grows forward, never shrinks.

## Permission Levels

| Actor | Permissions |
|---|---|
| **User** | Full control — can do anything including delete |
| **Main Claude** | Pre-approved: read, write, edit, commit, push, gh CLI. Must ask before any delete unless user has explicitly pre-authorized it. |
| **Minions** | Research only — read, search, web-fetch. No write, commit, push, or delete. |

## Workflow Order

Always follow this sequence. Never skip ahead:

1. **Open a GitHub issue** — captures the problem/feature before any code
2. **Update design/architecture docs** — ADR if warranted, roadmap, architecture
3. **Implement code** — with tests
4. **Update docs** — test-scenarios, user examples, troubleshooting callouts
5. **Close the issue** — with version, commit, and doc pointers

## Documentation Discipline

Every feature, breaking change, or config change ships with:
- Code + tests
- Test-scenario examples
- Troubleshooting callouts where relevant
- Architecture/design doc update if the shape changed
- ADR if a significant technology decision was made
- Roadmap `Resolved` update

Never ship a feature without its docs.

## Issue Lifecycle

- Close issues when work ships — include version, commit SHA, and doc pointers
- User-filed bugs: add `fixed — awaiting-feedback` label if user confirmation needed
- Dependabot patch/minor: merge when CI is green (ad-hoc, no milestone)
- Dependabot major: open an issue, add to a milestone, test intentionally
- Dependabot CVE: link PR to the security issue, include in relevant milestone

## SAFe Hierarchy (GitHub Issues)

| Level | GitHub object | When to use |
|---|---|---|
| **Milestone** | GitHub Milestone | PI / sprint container, ties to a semver release |
| **Feature** | Issue (`enhancement` label + milestone) | Single deployable unit of value — this is the default issue type |
| **Epic** | Issue (`epic` label, no milestone, task list of linked features) | Only when work spans multiple milestones |
| **Story** | Sub-task within a feature | Only when multiple contributors need independently assignable units |

Default: **milestone → feature**. Add epic and story layers only when the team size and cross-milestone scope justify the overhead.

## Technology Governance

Follow the original developer when a project forks due to governance or ownership breakdown. Validated examples:
- pfSense → **OPNsense** (m0n0wall founder Kasper endorsed it)
- CentOS → **Rocky Linux** (co-founder Kurtzer created it within days)
- nginx → watch **freenginx** (core developer Dounin forked over F5 governance)
- OpenOffice → **LibreOffice** (original developers moved there)

**Avoid:** Oracle, F5/nginx, Bitnami, Ubuntu in containers (use Debian slim).

**Preferred stack:**
- Container base (community): `python:slim-bookworm`
- Container base (production): `python:alpine`
- Container base (enterprise/FIPS): `cgr.dev/chainguard/python`
- CNI: Cilium
- Ingress: Kubernetes Gateway API (Cilium native)
- Secrets: HashiCorp Vault sidecar / CSI driver
- Postgres (single node): `postgres:16-alpine`
- Postgres (HA): CloudNativePG operator

## ADR Process

ADRs follow an RFC lifecycle: **Draft → Proposed → Accepted / Rejected / Withdrawn**. When a decision changes, a new ADR is created and the old one is marked **Superseded** — the historical record is never deleted.

## Git Commit Style

- Conventional commits: `feat:`, `fix:`, `docs:`, `chore:`, `ci:`, `refactor:`
- Co-author line on every commit: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
- Commit message: why, not what. The diff shows what; the message explains why.
- Never amend published commits. Create new commits instead.
- Never skip hooks (`--no-verify`) unless explicitly instructed.
