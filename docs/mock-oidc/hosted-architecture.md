# Hosted Architecture — mock-idp / lightweight-idp

**Status:** Design — pre-implementation
**Date:** 2026-05-19
**Covers:** Three-tier product model, domain strategy, routing architecture,
lab setup, and integration testing approach.

---

## Product tiers at a glance

| Tier | Who it's for | Hosted by | Domain |
|---|---|---|---|
| **Sandbox** | Anyone evaluating, no signup | KSA (shared, ephemeral) | `mock.ksatechnologies.dev/sandbox/` |
| **Pro** | Teams needing a stable CI identity plane | KSA (shared app, org-isolated) | `mock.ksatechnologies.dev/{org}/` |
| **Enterprise** | DR fallback, air-gapped, regulated, edge | KSA or self-hosted | `idp.ksatechnologies.com/{org}/` |

**Community** stays free / self-hosted / MIT forever — it is the distribution
engine and the top of the funnel.

---

## Why these domains

`mock.ksatechnologies.dev` — hosted services separated from the corporate
`ksatechnologies.com` domain. The `.dev` TLD is HSTS-preloaded (HTTPS enforced
by browsers), signals developer tooling, and costs ~$12/yr. Single wildcard
cert (`*.ksatechnologies.dev`) covers both Sandbox and Pro subpaths.

`idp.ksatechnologies.com` — Enterprise customers presenting a DR runbook or
compliance audit to a CTO / security architect cannot have `mock` in the URL.
`idp.ksatechnologies.com/{org}` reads as production identity infrastructure.
Same codebase, stricter SLA, hardened image, dedicated support.

---

## Growth funnel

```
Sandbox (no signup)
  → "I can configure claims to match my real IdP shape"
  → Pro signup ($29–49/month)
    → "I need a stable JWKS URL without a CI sidecar"
    → Enterprise (annual)
      → "This is our DR fallback / air-gapped identity plane"
```

The GitHub Actions marketplace action (`uses: thegrandwazoo/mock-idp-action@v1`)
is the parallel onramp for CI-first teams — one line of workflow config, no
signup, natural Pro upsell when they want persistent config.

---

## Routing architecture

All tiers run on a single Kubernetes cluster (Linode LKE + Cilium CNI).
Isolation is at the **Postgres `org_id` level**, not the pod level.

```
                        ┌─────────────────────────────────────┐
                        │  Linode LKE — Cilium CNI            │
                        │                                     │
internet ──────────────▶│  Gateway (Cilium native GA API)     │
                        │    mock.ksatechnologies.dev          │
                        │    idp.ksatechnologies.com           │
                        │         │                           │
                        │   ┌─────┴──────┐                   │
                        │   │ HTTPRoutes │                    │
                        │   └─────┬──────┘                   │
                        │         │                           │
                        │  ┌──────▼──────────┐               │
                        │  │  mock-idp pods  │ (shared)      │
                        │  └──────┬──────────┘               │
                        │         │                           │
                        │  ┌──────▼──────────┐               │
                        │  │    Postgres     │ (shared DB)   │
                        │  │  org_id scoped  │               │
                        │  └─────────────────┘               │
                        └─────────────────────────────────────┘
```

### HTTPRoute layout

```yaml
# Sandbox — fixed org, no auth, aggressive rate limit
HTTPRoute: sandbox
  hostnames: [mock.ksatechnologies.dev]
  matches:
    - path: /sandbox/
  backendRefs: [mock-idp-svc]

# Pro — org slug from path, API key required
HTTPRoute: pro-orgs
  hostnames: [mock.ksatechnologies.dev]
  matches:
    - path: /{org}/          # org slug extracted by app
  filters:
    - type: ExtensionRef     # Cilium per-org rate limit
  backendRefs: [mock-idp-svc]

# Enterprise hosted — same routing, separate hostname, stricter SLA
HTTPRoute: enterprise-orgs
  hostnames: [idp.ksatechnologies.com]
  matches:
    - path: /{org}/
  backendRefs: [mock-idp-svc]
```

The app reads the `{org}` slug from the path, looks up `org_id` in Postgres,
and scopes every query to that org. No per-tenant pods or namespaces needed
until a customer explicitly requires dedicated infrastructure (uncommon before
~$500/month ARR per org).

### What the app resolves from the path

```
GET mock.ksatechnologies.dev/acme-corp/entra-staging/token
                              └─────────┘ └───────────┘
                               org slug    issuer slug
```

1. Look up `acme-corp` → `org_id = 42` in Postgres
2. Validate API key from `Authorization: Bearer <key>` header
3. Scope all identity lookups to `org_id = 42`
4. Build `iss` claim: `https://mock.ksatechnologies.dev/acme-corp/entra-staging`
5. Issue token

### Sandbox differences

Sandbox is a pre-provisioned org (`org_slug = "sandbox"`) with:
- Read-only config (pre-baked identities, no config upload API)
- Hard rate limit: 10 req/min per source IP
- Short token TTL: 5 minutes max
- No audit log retention (in-memory only, 100 entries)

No provisioning logic. No API key. Zero signup friction.

---

## Per-org rate limiting

Enforced at the Cilium `HTTPRoute` layer via Cilium's `HTTPRoute` filter
extensions — no separate process, no extra cost, enforced in the eBPF dataplane.

```yaml
# Per-org rate limit via Cilium Gateway API extension
filters:
  - type: ExtensionRef
    extensionRef:
      group: gateway.cilium.io
      kind: CiliumHTTPRouteFilter
      name: rate-limit-pro-default
---
apiVersion: gateway.cilium.io/v1alpha1
kind: CiliumHTTPRouteFilter
metadata:
  name: rate-limit-pro-default
spec:
  rateLimit:
    requests: 600
    unit: minute   # 10 req/sec default for Pro
```

Override at provisioning time by patching the org's filter resource.

---

## Postgres schema (already shipped — v0.6.0)

Every table is keyed by `org_id`. Sandbox, Pro, and Enterprise orgs all
live in the same database, isolated by foreign key.

```
orgs            id, slug, tier, api_key_hash, created_at
tenants         id, org_id FK, name, provider, config JSONB
users           id, org_id FK, tenant_id FK, username, …
service_principals  id, org_id FK, …
token_events    id BIGSERIAL, org_id indexed, issued_at, grant_type, sub, aud, …
```

Schema supports CloudNativePG HA promotion at ~50 paying orgs without a
migration — same tables, just a new operator managing the Postgres cluster.

---

## Lab setup (basement k3s cluster)

The lab mirrors production so integration tests run against a realistic
OIDC stack. Goal: issue a token from mock-idp and have a real consumer
validate it.

### Stack

```
k3s (Proxmox VM or bare metal)
├── mock-idp           (this project, dev image)
├── oauth2-proxy       (OIDC consumer — free, MIT)
├── httpbin            (test backend — returns request headers as JSON)
└── Postgres           (postgres:16-alpine)
```

### Why oauth2-proxy instead of Kong OIDC

Kong's OIDC plugin requires Kong Enterprise (paid license). Kong Gateway
Community does not include OIDC. Alternatives:

| Tool | License | Role |
|---|---|---|
| **oauth2-proxy** | MIT | Sits in front of any service; validates OIDC tokens; most common OSS choice |
| **APISIX** | Apache 2.0 | Full API gateway (Kong-comparable feature set, free OIDC plugin) |
| **Envoy** | Apache 2.0 | JWT authentication filter built-in; used in Istio/service mesh contexts |
| **Traefik** | MIT | ForwardAuth middleware; OIDC via external auth service |

**Recommendation: oauth2-proxy for the lab.** It is the simplest to configure
against any OIDC provider, MIT-licensed, and widely used in Kubernetes
environments. It proves the full token → JWKS validation → claim assertion
flow without introducing a full API gateway.

### Lab quick-start

```bash
# 1. Run mock-idp
export CONFIG_PATH="config.example.yaml"
export ISS_BASE="http://mock-idp.local:8080"
uv run uvicorn mock_idp.main:app --port 8080

# 2. Run httpbin (the protected backend)
docker run -p 8081:80 kennethreitz/httpbin

# 3. Run oauth2-proxy configured against mock-idp
oauth2-proxy \
  --provider=oidc \
  --oidc-issuer-url=http://mock-idp.local:8080/entra-staging \
  --client-id=my-app \
  --client-secret=unused \
  --cookie-secret=$(openssl rand -base64 32) \
  --upstream=http://localhost:8081 \
  --http-address=:4180 \
  --skip-jwt-bearer-tokens=true \   # accept Bearer tokens (CI flow)
  --oidc-extra-audience=api://my-api

# 4. Hit the protected endpoint with a mock token
TOKEN=$(curl -s -X POST http://mock-idp.local:8080/entra-staging/token \
  -d 'grant_type=client_credentials&client_id=svc-account&client_secret=svc-secret&resource=api://my-api' \
  | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" http://localhost:4180/get
# → 200 with request headers including X-Forwarded-Email, X-Auth-Request-User
```

### APISIX alternative (Kong-like)

If you need a full API gateway with route management, APISIX is the closest
free alternative to Kong with native OIDC:

```bash
# Install APISIX via Helm (Apache-maintained chart)
helm repo add apisix https://charts.apiseven.com
helm install apisix apisix/apisix --set service.type=NodePort

# Configure OIDC plugin on a route (free — no license required)
curl -X PUT http://apisix-admin:9180/apisix/admin/routes/1 \
  -H 'X-API-KEY: changeme' \
  -d '{
    "uri": "/api/*",
    "upstream": {"nodes": {"httpbin:80": 1}, "type": "roundrobin"},
    "plugins": {
      "openid-connect": {
        "discovery": "http://mock-idp.local:8080/entra-staging/.well-known/openid-configuration",
        "bearer_only": true,
        "realm": "mock-idp"
      }
    }
  }'
```

---

## Integration tests in the test suite

Rather than requiring oauth2-proxy or APISIX in CI, the pytest suite validates
the full OIDC token lifecycle directly:

```
test_app.py already covers:
  ✓ Token issuance (password + client_credentials grants)
  ✓ JWKS endpoint returns correct keys
  ✓ /userinfo validates bearer token

Planned integration test additions (v0.7.0):
  ✓ Issue token → fetch JWKS → validate signature locally (joserfc)
  ✓ Issue token → validate claims (iss, aud, roles, provider-specific fields)
  ✓ Org-slug routing: request to /{org}/{issuer}/token scopes correctly
  ✓ Sandbox: rate limit enforced at 10 req/min
  ✓ API key gating: 401 without key on Pro route, 200 with key
```

The token validation tests use `joserfc` (already a project dependency) to
replicate exactly what a real consumer would do against the JWKS endpoint —
no external process required in CI.

---

## Build sequence for v0.7.0

1. **Org provisioning API** — `POST /api/orgs` → creates slug, hashes API key, writes to Postgres
2. **Org-slug routing in app** — extract `{org}` from path prefix, resolve `org_id`, scope queries
3. **Config upload API** — `PUT /api/orgs/{slug}/config` → stores YAML, triggers hot-reload
4. **Sandbox org** — pre-seeded in Alembic migration, read-only, hard rate limit
5. **Helm chart updates** — HTTPRoute manifests for Sandbox, Pro, and Enterprise hostnames
6. **Integration tests** — pytest coverage for slug routing, API key auth, sandbox limits
7. **GitHub Actions marketplace action** — `uses: thegrandwazoo/mock-idp-action@v1`

---

## Related docs

- [pro-enterprise.md](pro-enterprise.md) — tier strategy, pricing, build priority
- [business-model.md](business-model.md) — commercial model, à la carte hooks
- [roadmap.md](roadmap.md) — v0.7.0 milestone issues
- [hosting-poc.md](hosting-poc.md) — platform evaluation (DOKS vs Civo vs LKE)
- [ADR-006](ADR-006-multi-provider-expansion.md) — multi-provider claim architecture
