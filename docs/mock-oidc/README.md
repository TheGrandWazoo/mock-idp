# mock-idp — Documentation Index

A FastAPI + authlib mock OIDC/OAuth2 server for testing API gateway
authentication. Supports `password` and `client_credentials` grants
with per-identity token shape (v1/v2), per-identity audience allowlisting
(lax / strict mode), admin override, admin key rotation, CORS, a browser
token playground, and debug endpoints.

---

## Why this exists

Testing an API gateway's OIDC validation logic requires a server that can
emit tokens on demand with precise, controllable claim shapes — v1 vs v2
layouts, specific audiences, selective optional claims, expired tokens,
wrong-signature tokens, and multiple issuer URLs. Static third-party mock
servers cover some of these scenarios natively; the rest require
implementation-specific callback code and a container rebuild per
variation, which makes the test loop expensive and keeps coverage narrow.

A custom Python server covers all of it natively plus several ergonomic
features (token playground, debug endpoints, admin key rotation) that
off-the-shelf tools can't easily provide. The implementation is small
enough to read end-to-end in one sitting and easy to extend with a new
route handler per scenario.

---

## What's in this writeup

| File | Purpose |
|---|---|
| `ADR-001-python-mock-oidc.md` | Decision rationale, alternatives considered, consequences |
| `roadmap.md` | v0.3 candidates, parked ideas, maybe-never items; resolved questions log |
| `docs/architecture.md` | Stack, endpoint shape, identity store schema, grant types, flows (diagrams), v0.2 ergonomics |
| `docs/implementation-guide.md` | Project layout, config schema, Dockerfile, K8s manifest, local dev, sample requests |
| `docs/test-scenarios.md` | 45 concrete scenarios mapped to curl invocations and gateway assertions |
| `briefs/stakeholder-brief.md` | Why a custom server, what it costs, what it earns |
| `briefs/operational-brief.md` | Day-2 ops: footprint, deploy, monitor, sunset criteria |
| `playground.html` | Browser-based token playground; served by the app at `GET /`. Self-contained, no build step. |

---

## Quick orientation

- **Deciding whether to build this:** `briefs/stakeholder-brief.md` → `ADR-001-python-mock-oidc.md` → `roadmap.md`
- **Building it:** `ADR-001-python-mock-oidc.md` → `docs/architecture.md` → `docs/implementation-guide.md` → `docs/test-scenarios.md`
- **Operating it post-deploy:** `briefs/operational-brief.md` → `docs/test-scenarios.md`
- **Picking the next feature:** `roadmap.md`

---

## Deployment model

The mock runs as a single pod in its own namespace (`mock-idp`) alongside
whatever API gateway or service it supports. It is **not** a replacement
for production identity infrastructure — it is a test fixture that happens
to speak OIDC. Deploy it at a separate ingress (e.g.,
`mock-idp.example.com`) and point gateway routes at the relevant issuer
URL for each test scenario.
