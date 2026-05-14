# Stakeholder Brief — Python Mock OIDC

**For:** team leads, architects, anyone deciding whether this gets built.
**Reading time:** 3 minutes.

---

## The problem in one paragraph

Testing an API gateway's OIDC validation plugin thoroughly requires a
token source that can emit tokens with precise, controllable claim shapes
— v1 vs v2 layouts togglable on demand, two grant types (user-password
and service-to-service), per-identity audience allowlisting, selective
optional claims, expired tokens, signature failures, and
admin-controlled negative cases. Existing third-party mock OIDC servers
handle a subset of these scenarios natively; the rest require
implementation-specific callback code (typically in a different
language), a container rebuild, and a redeploy per variation. That makes
the test loop expensive enough that coverage stays narrow.

---

## The proposal in one paragraph

Build a custom Python mock OIDC server (FastAPI + authlib, modular
package, two focused days of work) that handles all of it natively, plus
a few ergonomic features that materially shorten the test loop: a
browser-based token playground, debug endpoints (decode any JWT, inspect
loaded identities, inspect runtime config), admin-controlled key
rotation, and configurable CORS. Deploy in its own namespace alongside
any existing test infrastructure. Existing static mocks continue to
serve static fixture tests; the Python mock handles everything else.

---

## What it earns

- **Test coverage that's actually possible.** Forty-five concrete
  scenarios across token shape, grants, audience gating, claim overrides,
  signature failures, and JWKS rotation — all exercised by changing a
  header or a form field. No rebuild, no redeploy.
- **Fast iteration.** `uvicorn --reload` reflects edits in ~1 second.
  New scenario? Add a route handler.
- **Ergonomics that pay back daily.** The token playground lets
  non-developers issue and inspect tokens via a browser. Debug endpoints
  answer "what's in this token?" and "what's loaded?" without shelling
  into pods.
- **Readable by the whole team.** Python is widely known. New engineers
  can extend the mock without learning an unfamiliar toolchain.
- **No external dependency for the dynamic parts.** No upstream tool to
  track for per-request shape, audience gating, or admin override
  surfaces.

---

## What it costs

- **One to two engineering days** to build (incl. playground HTML,
  tests, Helm chart, smoke validation).
- **Ongoing ownership.** A small Python package that the maintainer
  keeps forever. Most is straightforward FastAPI + authlib; the
  OAuth2-spec edges (clock skew, JWKS rotation, error response shapes,
  kid matching) are where bugs hide.
- **A second deployment** if a static third-party mock is also in use:
  two ingresses, two Deployments, two monitoring surfaces. Operationally
  cheap given the small footprint, but nonzero.
- **Onboarding cost.** Each new engineer reads the implementation rather
  than an upstream README.

---

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| OAuth2 spec edge bugs surface during production-style tests | Medium | Pin to the documented test scenarios; explicitly out of scope for production OIDC; sunset clause keeps mission narrow |
| Bus factor — only one engineer understands the code | Low (Python is widely known) | Documentation in this writeup; tests for each scenario; deliberately small surface area |
| Signing key resets on pod restart break in-flight tests | Medium | Documented and accepted; `replicas: 1` enforced; test harness re-acquires tokens on session start |
| Spec drift — identity provider changes claim semantics or gateway tightens validation | Medium | Patch the relevant route handler; estimated < 1 day per drift event |
| Admin override or playground endpoints accidentally exposed publicly | Low (internal-only ingress) | Internal DNS, internal CA, no internet path; admin endpoints gated by `X-Admin-Token` |

---

## Decision posture

This is a **build-vs-buy** call where the "buy" side is largely in hand
(a static mock + a JS-callback mock side-by-side would cover most
scenarios with no Python maintenance). The "build" side wins on:

- Test iteration speed (route handler edit beats callback config and
  container rebuilds).
- Language familiarity.
- Ergonomics that aren't available off-the-shelf (token playground,
  debug-decode, admin key rotation).

The "build" side loses on net-new code to maintain. Both options are
reasonable; neither is clearly wrong. The question is whether you prefer
carrying a small bespoke tool with rich ergonomics or a slightly larger
third-party surface area with fewer creature comforts.

---

## Recommendation

If you have a focused day or two available in the next sprint and the
team is Python-fluent (it is), **build it**. The implementation is small
enough that the maintenance burden is bounded. The test-iteration win is
real and recurring — every future gateway test that needs custom claim
shapes, audience scenarios, or admin-controlled overrides benefits.

If you don't have the time, deploy a JS-callback mock alongside the
static mock and move on. That outcome is also fine — it covers the core
normalization scenarios without the ergonomic extras.

What's **not** recommended: extending a static mock with complex
callback code in an unfamiliar language. That is the worst of both
worlds — code maintenance *plus* slow iteration.

---

## Sunset criteria

This mock retires when one of:

1. Production confirms the identity provider is v2-only with no plan to
   revert — the v1 shape testing becomes obsolete and a simpler fixture
   covers what's left.
2. The API gateway replaces its OIDC plugin with something that doesn't
   need this level of token-shape testing.
3. A richer test framework is adopted that absorbs this functionality.

For roadmap items that haven't yet been built but represent natural
evolutions (Token Exchange for gateway-as-intermediary, introspection,
per-issuer signing keys, secret management integration, etc.), see
`roadmap.md`.
