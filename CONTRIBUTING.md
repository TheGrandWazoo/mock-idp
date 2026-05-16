# Contributing to mock-idp

Three ways to help: file a bug, propose a feature, or submit a patch.

---

## Filing a bug

A good bug report lets us reproduce the problem without guessing. Include all
of the following:

### What to include

**1. Version and environment**

```
mock-idp version: (run `grep ^version pyproject.toml`, or check the Docker image tag)
Backend: yaml | postgres
Deployment: local uv run | Docker | Kubernetes
Python version: (uv run python --version)
OS: (if running locally)
```

**2. Minimal config snippet**

Paste the relevant section of your config. Redact real passwords and secrets —
replace them with `REDACTED` or a placeholder. We need enough structure to
load identities and reproduce the issue; we do not need production credentials.

```yaml
# Example — strip it down to only what's relevant
tenants:
  22222222-2222-2222-2222-222222222222:
    provider: entra_id
    service_principals:
      my-service:
        client_id: 01010101-...
        secret: REDACTED
        signing_alg: ES256
```

**3. Exact reproduction steps**

Paste the exact curl command (or equivalent) that triggers the bug. Use
`--verbose` or `-v` to capture headers:

```bash
curl -v -X POST http://localhost:8080/default/token \
  -d "grant_type=client_credentials" \
  -d "client_id=my-service&client_secret=REDACTED" \
  -d "resource=api://my-api"
```

**4. Expected result vs actual result**

```
Expected: 200 with access_token
Actual:   500 Internal Server Error
```

**5. Full error output**

- If the server logged an exception, paste the full traceback (not just the
  last line).
- If the gateway is rejecting the token, paste the gateway's error log and
  the output of:

```bash
curl -s -X POST http://localhost:8080/debug/decode \
  -H "Content-Type: application/json" \
  -d '{"token": "<paste token here>"}'
```

The decode endpoint shows `header`, `payload`, and
`signature_validated_against_published_key`. That single field tells us
immediately whether the problem is key mismatch, claim content, or something
upstream.

**6. JWKS output** (for signature or algorithm issues)

```bash
curl -s http://localhost:8080/default/jwks | jq '[.keys[] | {kid, kty, crv}]'
```

**7. Config lint output** (for startup failures)

```bash
CONFIG_PATH=your-config.yaml ISS_BASE=http://localhost:8080 uv run python -c \
  "import asyncio; from mock_idp.config import reload_config; asyncio.run(reload_config())"
```

This runs the same config load path the server uses and surfaces Pydantic
validation errors before the process starts.

### Where to file

Open an issue at <https://github.com/TheGrandWazoo/mock-idp/issues>.
Use the title format: `bug: <short description>`.

---

## Proposing a feature

Before opening a feature issue, check [`docs/mock-oidc/roadmap.md`](docs/mock-oidc/roadmap.md).
If it's already a candidate item there, comment on the relevant section with
your use case — that provides the concrete demand that moves things up the
priority list.

If it's not on the roadmap, open an issue with:

1. **The concrete test scenario that requires it.** "I need to test that my
   gateway rejects X when Y" is more actionable than "it would be nice to have Z."
2. **What you'd need to configure.** Show the YAML shape you'd want.
3. **What the response should look like.** A curl example + expected JSON is ideal.

Use the title format: `feat: <short description>`.

---

## Submitting a patch

### Development setup

```bash
# Install Python dependencies
uv sync

# Install and wire pre-commit hooks
uv tool install pre-commit
pre-commit install

# Set required environment variables
export CONFIG_PATH="config.example.yaml"
export ISS_BASE="http://localhost:8080"

# Run the server
uv run uvicorn mock_idp.main:app --reload --port 8080

# Run tests
uv run pytest tests -v
```

All tests must pass and ruff must be clean before opening a PR:

```bash
uv run ruff check src tests
uv run pytest tests -v
```

### Branch and commit conventions

Branch from `main`. Name your branch descriptively:

```
feat/webhook-on-token-issuance
fix/es256-wrong-sig-endpoint
docs/add-realm-roles-scenario
```

Commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/)
style used throughout this repo:

```
<type>: <short present-tense description> (<version if applicable>)
```

Types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`.

Examples from this repo:

```
feat: configurable signing algorithm per identity — RS256/ES256 (v0.5.1)
fix: guard SARIF upload against missing file; upgrade codeql-action to v4
docs: full documentation pass for v0.4.1–v0.5.0
chore: add Dependabot and upload Trivy results to GitHub Security tab
```

Keep the subject line under 72 characters. Use the body for "why", not "what" —
the diff shows what changed.

### Code style

- **Formatter / linter:** `ruff`. Configuration is in `pyproject.toml`. The
  pre-commit hook runs it on every commit; CI runs it on every push.
- **Types:** All new functions must have type annotations. No `Any` except
  where the YAML schema genuinely allows arbitrary values.
- **Comments:** Only when the *why* is non-obvious — a hidden constraint,
  a workaround for a specific bug, a subtle invariant. Don't describe what
  the code does; well-named identifiers do that.
- **No backwards-compatibility shims:** If you rename or remove something,
  delete the old code. Don't leave `# removed` comments or re-export aliases.

### Test requirements

Every code change needs tests:

- **New endpoint or grant type:** integration test in `tests/test_app.py`
  covering the happy path and at least one rejection/error path.
- **New config field:** test that a valid value is accepted and an invalid
  value raises a Pydantic validation error at config load time.
- **Bug fix:** add a test that fails before the fix and passes after. Name
  it after the behaviour it asserts, not the bug.
- **Negative / security test:** use the existing patterns (`X-Test-Expired`,
  `X-Test-Fail`, wrong-sig endpoint) rather than building new machinery.

Keep the test module scope (`scope="module"`) fixture in place — it
shares one `TestClient` across all tests, which matters for per-issuer
key-store state.

### Documentation requirements

Every feature or breaking change must ship with docs **in the same commit**:

| Change type | What to update |
|---|---|
| New endpoint | Endpoint table in `docs/mock-oidc/docs/architecture.md` |
| New config field | Field reference table in `architecture.md` |
| New feature (any) | New scenarios in `docs/mock-oidc/docs/test-scenarios.md` with a "What this is / Why you'd use it" header and a Troubleshooting callout |
| Non-obvious design decision | New `ADR-00N.md` in `docs/mock-oidc/` |
| Version bump | `docs/mock-oidc/roadmap.md` — add to Resolved, bump "Current release", remove from candidates |

The process note at the bottom of `roadmap.md` says it plainly: *every roadmap
item should land with code, tests, a test-scenario doc entry, and a config
schema update if applicable*. PRs without docs will be asked to add them before
merge.

### PR checklist

Before marking your PR ready for review:

- [ ] `uv run ruff check src tests` — no errors
- [ ] `uv run pytest tests -v` — all tests pass
- [ ] `pre-commit run --all-files` — all hooks pass
- [ ] New tests added for all new behaviour
- [ ] `docs/mock-oidc/docs/test-scenarios.md` updated (new scenarios + troubleshooting)
- [ ] `docs/mock-oidc/docs/architecture.md` updated (endpoints, field reference, key handling)
- [ ] `docs/mock-oidc/roadmap.md` updated (version bump + Resolved entry)
- [ ] `pyproject.toml` version bumped (patch for fixes, minor for features)
- [ ] `uv lock` run after any dependency change

### PR description template

```markdown
## What this changes

<!-- One paragraph. What is the user-visible change? -->

## Why

<!-- The concrete test scenario or bug that motivated this. -->

## Config change

<!-- If the YAML schema changed, show the before/after. "No config change" if not. -->

## Test coverage

<!-- List the new or updated tests and what each one asserts. -->

## Checklist

- [ ] ruff clean
- [ ] All tests pass
- [ ] Docs updated (test-scenarios, architecture, roadmap)
- [ ] Version bumped in pyproject.toml
```

---

## What we will not merge

- Code without tests.
- Tests without docs (for user-facing features).
- Security bypasses: `--no-verify`, hardcoded admin tokens in tests against
  real deployments, endpoints that skip authentication unconditionally.
- Changes to the Postgres backend without a corresponding Alembic migration.
- Force-pushes to `main`.
