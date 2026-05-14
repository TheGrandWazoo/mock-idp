# Operational Brief — Python Mock OIDC

**For:** SREs and platform operators running the mock day-to-day.
**Reading time:** 3 minutes.

---

## What it is

A FastAPI app that emits configurable OIDC-compliant JWTs for API
gateway OIDC plugin testing. Single Python process per pod, no
persistence, no external dependencies. Lives at:

- **Namespace:** `mock-idp`
- **Internal Service:** `mock-idp.mock-idp.svc.cluster.local:8080`
- **Ingress:** `mock-idp.example.com` (whatever ingress class your cluster uses)
- **Browser-facing surface:** token playground at `/`, debug endpoints
  under `/debug`, admin endpoints under `/admin` (gated by
  `X-Admin-Token`).

---

## Deploy footprint

| Resource | Value |
|---|---|
| Replicas | **1** (do not scale — signing key is per-pod, see Failure modes) |
| CPU request / limit | 50m / 200m |
| Memory request / limit | 64Mi / 128Mi |
| Image | `ghcr.io/your-org/mock-idp:<tag>` |
| Image base | `python:3.14-slim` |
| Persistent volumes | none |
| ConfigMaps | `mock-idp-config` (identity store) mounted at `/etc/mock-idp/` |
| TLS | Secret referenced by Ingress TLS block |

Idle: ~25 MB resident, near-zero CPU. Under 100 RPS: ~50 MB, ~50 mCPU.
Token playground renders are one-shot per operator; negligible impact.

---

## Health and readiness

- **Liveness:** `GET /healthz` every 30s after a 10s initial delay.
- **Readiness:** `GET /healthz` every 5s after a 2s initial delay.

Both return `{"status": "ok"}` whenever the FastAPI loop is responsive.
If the pod ever fails readiness, the Python process is stuck or the
event loop is wedged. Restart is the only remedy — no state persists.

---

## Operator surfaces

### Token playground — `GET /`

A self-contained HTML page where operators can pick an identity, pick a
destination audience, click "Issue token", and see the resulting JWT
plus decoded claims plus copy-to-clipboard snippets. Useful for ad-hoc
testing without writing curl.

### Debug endpoints

- `POST /debug/decode` — decode any JWT
- `GET /debug/identities` — loaded user and client store (secrets
  redacted to `"***"`)
- `GET /debug/config` — effective runtime config: auth_mode, CORS
  origins, ISS_BASE, identity counts, signing kid thumbprint

All three return JSON. Useful for "is the mock loaded with what I think
it is" investigations.

### Admin endpoints

- `POST /admin/rotate-jwks` — generates a new signing keypair, replacing
  the in-memory one. Previously-issued tokens stop validating. Used to
  exercise the gateway's JWKS-cache-invalidation behavior.

Gated by `X-Admin-Token: <token>` where the token comes from the config
file's `admin_token` field. Rejected with 403 on mismatch.

---

## Monitoring

The mock does not emit Prometheus metrics out of the box. If you want
visibility:

- **Add `fastapi-instrumentator`** as a dependency and one line of code
  to expose `/metrics`. Then add a `ServiceMonitor` as appropriate for
  your cluster's observability stack. ~15 minutes of work.
- **Or scrape access logs** via your log aggregator (Loki, Splunk, ELK,
  etc.) — uvicorn writes one line per request to stdout, structured
  enough to filter on status code.

For a test fixture, "scrape access logs" is almost always sufficient.
Don't over-build observability for a tool whose failures degrade tests,
not production.

---

## CORS

`CORSMiddleware` is wired during app startup. Allowed origins come from
`cors_allow_origins` in the config; default `["*"]` is sufficient for
any test-fixture use case.

If browser-based test clients fail with CORS errors, check
`cors_allow_origins` and confirm the middleware is wired in the loaded
config (`GET /debug/config`).

---

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| Tests fail with `401 invalid signature` after a deploy, pod restart, or `/admin/rotate-jwks` call | Signing key rotated → JWKS cached by the gateway is stale | Wait for the gateway's JWKS cache TTL (typically 60–300s) or restart the gateway to force re-fetch |
| `503` from the gateway on a route configured for this mock's issuer | Pod is down or readiness failing | `kubectl get pods -n mock-idp -l app.kubernetes.io/name=mock-idp` — restart if not Ready |
| `404` from the mock's `/{issuer}/...` endpoints | Wrong issuer slug in the URL | Any URL-safe slug works — verify the test client's URL matches what the gateway is configured for |
| `400 invalid_target` from the token endpoint | `auth_mode: strict` and the requested resource isn't in the identity's `allowed_audiences` | Add the audience to the identity's allowlist, or switch to lax mode for that test |
| `401 invalid_grant` / `401 invalid_client` | Wrong password / secret for the named identity | Verify against `GET /debug/identities` (passwords redacted, but identity presence confirmed) |
| Multiple replicas inadvertently created | Different replicas have different signing keys → tokens may fail validation depending on which replica served | Scale back to 1 replica; ensure no HPA or automation overrides this |
| `/admin/rotate-jwks` called accidentally | Existing in-flight tokens reject until clients reacquire | Document the test pattern; `admin_token` gating prevents accidental curls |
| Tests pass locally, fail in cluster | Ingress or DNS misconfiguration | Confirm the configured `ingressClassName` is set in the Ingress manifest; check ingress controller logs |
| Browser-based test client hits CORS error | `cors_allow_origins` doesn't include the test client's origin | Update `cors_allow_origins` in the ConfigMap, restart pod |
| Pod fails to start | Malformed config YAML | Fix YAML, re-apply ConfigMap, restart |

No data persistence, so no data-loss failure modes.

---

## Image lifecycle

- Built and pushed by CI on every push to `main` (tagged `sha-<hash>`)
  and on version tags.
- Tag scheme:
  - Pre-release (`v1.2.3-alpha.1`): version tag only, no `latest`
  - Release (`v1.2.3`): version tag + `latest`
- Dependency bumps on a quarterly cadence. Pinned in `pyproject.toml`
  with a locked `uv.lock` — no floating versions.
- Trivy scans for CRITICAL/HIGH CVEs on every build; blocks on findings.

---

## What to verify after a deploy

1. `kubectl get pods -n mock-idp -l app.kubernetes.io/name=mock-idp`
   → Pod `Ready` within 30s.
2. `curl https://mock-idp.example.com/debug/config` → returns
   expected `auth_mode`, identity counts, signing kid.
3. `curl https://mock-idp.example.com/default/.well-known/openid-configuration`
   → Returns discovery JSON with correct `issuer`.
4. `curl https://mock-idp.example.com/default/jwks` → JWKS with one key.
5. Token round-trip via the playground at `https://mock-idp.example.com/`
   — pick alice, audience `api://serviceB`, click "Issue", inspect
   decoded claims.
6. A protected-route test against your gateway:
   - `POST /default/token` → get JWT
   - `GET` protected route with `Authorization: Bearer <jwt>`
   - Confirm upstream sees the expected normalized headers.

Steps 1–4 take ~30 seconds. Step 5 is the manual smoke test. Step 6 is
the real integration smoke test.

---

## Configuration changes

The identity store lives in `mock-idp-config` ConfigMap. Edits require
a pod restart:

```bash
kubectl -n mock-idp edit configmap mock-idp-config
kubectl -n mock-idp rollout restart deployment/mock-idp
```

After restart, verify the new state with `GET /debug/identities` and
`GET /debug/config`.

Hot reload is on the roadmap; not in v0.2.

---

## Network reachability

- **Gateway → Python mock:** in-cluster via Service DNS. Verify any
  NetworkPolicy allows TCP/8080 from gateway pods to `mock-idp`
  namespace.
- **External test client → Python mock:** via the ingress hostname. TLS
  via the secret referenced in the Ingress TLS block.
- **Internal DNS:** `mock-idp.example.com` must resolve to the cluster's
  ingress VIP. Managed by the DNS team.

If the cluster has strict NetworkPolicy:

- Ingress on tcp/8080 from gateway pods
- Ingress on tcp/8080 from the ingress proxy
- Ingress on tcp/8080 from any test-runner pods that hit the mock directly

---

## Cost to run

Per-pod resource ask is trivial. The real cost is operator attention
when something breaks. Mitigated by:

- The mock is non-customer-facing — failures don't page.
- The architecture is small (one Python process); diagnosis is fast.
- Pod restart is the universal remedy.
- The debug endpoints (`/debug/identities`, `/debug/config`,
  `/debug/decode`) reduce time-to-diagnosis substantially.

---

## Kong OIDC integration notes

This section covers observed behavior specific to Kong's OIDC plugin with
claims-to-headers and a downstream header normalization function.

### Claims-to-headers mapping

Kong's OIDC plugin maps JWT claims to HTTP headers before forwarding the
request upstream. The mapping is case-sensitive — if the plugin config says
`roles → X-User-Roles`, the claim name in the token must be exactly `roles`.

The v1 and v2 token shapes both emit `roles` and `groups` at the top level, so
either shape works for Kong claim mapping. The difference between shapes is the
surrounding identity fields (`oid`, `upn`, `preferred_username`, `appid` vs
`azp`). Check which claims your normalization function reads and pick the shape
that matches.

### Array claims are base64-encoded by Kong

Kong base64-encodes array-valued claims before writing them as headers. This
is intentional — raw JSON arrays (`["operator","responder"]`) contain
characters that can break HTTP header parsing.

Your normalization function should decode before forwarding upstream:

```
X-User-Roles: WyJvcGVyYXRvciIsInJlc3BvbmRlciJd
                 ↓ base64-decode
["operator","responder"]
```

**Edge cases worth testing in mock-idp:**

| Scenario | How to reproduce |
|---|---|
| Empty array | Set `roles: []` on the identity in config |
| Single item | Set `roles: [operator]` |
| Multiple items | Default alice: `roles: [operator, responder]` |
| Claim absent entirely | Use `X-Omit-Claims: roles` request header when fetching the token |

The `X-Omit-Claims` header tells mock-idp to drop named claims from the token
before signing. Use it to test "what does the normalization function do when
the roles header is missing?"

### Admin token in headers

The `X-Admin-Token` header for `/admin/rotate-jwks` is a plain string header —
not a Bearer token — so it is not subject to the base64 array encoding. It
passes through Kong unchanged if the route is exposed through the gateway.

### JWKS caching and key rotation

Kong caches the JWKS response. After a `/admin/rotate-jwks` call, previously
issued tokens will fail validation until the cache expires (typically 60–300s
depending on plugin config). Use this deliberately to test the gateway's
handling of signature validation failure after key rotation.

To force an immediate re-fetch without waiting for the TTL, restart the Kong
pod or call the Kong admin API to clear the OIDC plugin cache if your version
supports it.

### Audience validation

Kong's OIDC `config.audience` field checks the `aud` claim in the token. Set
`auth_mode: strict` in mock-idp and configure `allowed_audiences` on the
identity to test that Kong correctly rejects tokens with a mismatched audience.
In `lax` mode, mock-idp issues tokens for any requested audience regardless of
the identity's allowlist — useful for getting tokens into Kong during setup, but
switch to strict for audience-validation test cases.

---

## When to decommission

See `briefs/stakeholder-brief.md` §Sunset criteria. Decommissioning is a
single Helm/manifest delete + DNS record retirement + ConfigMap delete.
No data migration.
