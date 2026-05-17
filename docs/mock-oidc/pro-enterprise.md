# Pro / Enterprise — Strategy & Roadmap

mock-idp is MIT-licensed and self-hosted. Monetization works by selling the
*workflow and infrastructure around it*, not the tool itself. The tool stays
open source — that is the distribution engine.

See [business-model.md](business-model.md) for the commercial strategy,
pricing rationale, à la carte hooks, and the mock-idp → lightweight-idp
product evolution story.

---

## Tier overview

| Tier | Pricing model | Delivery |
|---|---|---|
| **Community** | Free / open source | Self-hosted, ghcr.io image |
| **Pro** | ~$29–49 / month per org | Hosted SaaS + enhanced features |
| **Enterprise** | Annual, negotiated | Self-hosted Pro + SLA + integrations |

---

## Community (current)

MIT license, self-hosted, `pip install mock-idp` or `ghcr.io/thegrandwazoo/mock-idp`.
GitHub Issues for support. This is the top-of-funnel and remains fully functional.

**Rate limiting (OSS):** `slowapi` middleware, configurable via env vars
(`MOCK_IDP_RATE_LIMIT=60/minute`). Token bucket per source IP and per
`client_id`. Exposes rate limit state on `/healthz` so a Kubernetes readiness
probe can shed load when the instance is saturated. Protects self-hosted
instances from runaway CI pipelines without requiring an API gateway.

---

## Pro

### 1. Hosted mock endpoint (highest priority)

> User decision: this is the first Pro feature to build.

A team gets a persistent subdomain:
`https://mock.ksatechnologies.com/{org-slug}/{issuer}/token`

**Why this first:**
- Zero setup — works from ephemeral CI runners without a sidecar container
- Eliminates the main friction point for new adopters
- Routing layer is Cilium Gateway API — `GatewayClass` → `Gateway` → per-org `HTTPRoute`
- Billing surface: per-org slug gated behind an API key

**Implementation sketch:**
- Slug registry (Postgres): `org_slug → config_id`
- Provisioning API: `POST /api/orgs` → creates slug, returns API key
- Config upload: `PUT /api/orgs/{slug}/config` → stores YAML, triggers hot-reload
- Routing: `HTTPRoute` per org slug → mock-idp backend `Service`

**Postgres subchart:** Use a minimal custom subchart wrapping the official
`postgres:16-alpine` image (see [Postgres / Helm notes](#postgres--helm-notes)
below). Do not use `bitnami/postgresql` — Bitnami moved their maintained images
to a paid subscription in 2024; the public Docker Hub images are stale and
not kept up for security.

---

### 2. Token audit log

Every `POST /token` call stored with: timestamp, issuer, grant type, identity,
audience, resolved roles, client IP. Accessible via:
- Web UI table with filtering
- `GET /api/orgs/{slug}/audit-log` for programmatic access

**Why this second:** Turns "why did my test fail" into a debuggable artifact.
Especially valuable for flaky CI — correlate token issuance with test timing.

**Backend:** Postgres (the same instance as the hosted endpoint registry).
Retention: 30 days on Pro, configurable on Enterprise.

---

### 3. Web admin UI

> User decision: the admin UI is built on top of the Postgres backend.

A web form for creating and editing identities, tenants, and grants without
touching YAML. Backed by the `PostgresIdentityStore` (v0.4.0).

**Why after Postgres backend:** The Postgres store already has the CRUD surface.
The UI is a thin layer on top of it, not a new storage system.

**Scope:**
- Create / edit / delete users, service principals, tenants, client apps
- Preview resolved roles for a given identity + audience before issuing
- No need to replicate the playground — link to it for token issuance

**Auth:** SSO/SAML gated (Pro uses the org API key; Enterprise ties into the
customer's IdP — see Enterprise section).

---

### 4. Rate limiting — Cilium Gateway API on hosted endpoint

The hosted endpoint uses Cilium's native Gateway API implementation.
Per-org rate limiting is enforced at the `HTTPRoute` layer via Cilium
HTTPRoute filter extensions — no separate API gateway process, no extra
Postgres instance.

Per-org limits are set at provisioning time by patching the org's
`HTTPRoute` resource. Cilium enforces them in the eBPF dataplane.

Community/self-hosted instances use the in-app `slowapi` rate limiter
(single-instance; see Community section above).

**Why not Kong:** Kong is a strong integration *target* (mock-idp issues
tokens that Kong validates — see ecosystem docs). Running Kong as our own
infrastructure layer adds a second Postgres instance and significant
operational overhead before we have a single paying customer. Cilium
Gateway API replaces it cleanly and we already run Cilium as the CNI.
Revisit Kong for the hosted endpoint if we need a developer portal,
publishable API catalog, or per-customer analytics dashboards — those are
post-$10k/month concerns.

### 5. Webhook reliability

The v0.5.2 webhook delivery is fire-and-forget. Pro adds:
- Delivery retries with exponential backoff (3 attempts)
- Dead-letter queue for failed deliveries
- Replay-from-UI for debugging

**When to build:** after hosted endpoint ships and a customer asks for it.
Already in the parked roadmap (see [roadmap.md](roadmap.md)).

---

## Enterprise — lightweight-idp

Enterprise is where mock-idp becomes **lightweight-idp**: a production-grade,
security-hardened OIDC identity plane for environments that need real tokens
but cannot depend on a live Entra ID / Okta / Cognito tenant.

**Upgrade triggers to watch for:**
- "Our staging cluster is air-gapped — we want this running permanently, not
  just in CI."
- "Our DR plan assumes Entra ID might be unavailable. We need a local fallback
  for internal services."
- "Legal flagged CI making outbound calls to our production Entra ID tenant."
- "Our edge cluster has no internet access and needs a local OIDC plane."

When a customer says any of these, the conversation shifts from "testing tool"
to "identity infrastructure supplement." The pricing and contract change too.

### Security hardening (lightweight-idp differentiators)

**FIPS 140-2 mode**
`MOCK_IDP_FIPS=true` switches all crypto to OpenSSL FIPS provider.
Required for FedRAMP, DoD, and many financial services environments.
Delivered as a separate image tag: `ghcr.io/thegrandwazoo/mock-idp-enterprise:fips`.

**CIS Level 2 hardened image**
Container image built against CIS Docker Benchmark Level 2:
non-root user, read-only filesystem, no unnecessary capabilities, minimal
base image (distroless or UBI minimal). Scanned with Trivy on every build;
SARIF results in the Security tab. Customers can cite this for their own
compliance audits.

**Vault sidecar integration**
HashiCorp Vault Agent Injector pattern: Vault injects secrets as files;
mock-idp reads them via the existing `from_file` secret reference (v0.5.4).
The Enterprise Helm chart ships:
- Example Vault policy (`mock-idp-policy.hcl`)
- Vault Agent annotation templates for the Deployment
- AWS Secrets Manager and Azure Key Vault alternatives via CSI driver

Enterprises won't put client secrets in ConfigMaps. The `from_file` foundation
is already there; the Enterprise chart makes it turnkey.

**Cilium network policy**
Kubernetes `NetworkPolicy` manifests using Cilium CiliumNetworkPolicy CRDs:
- Ingress: only Gateway API pods (Cilium envoy proxy) can reach mock-idp pods
- Egress: only webhook destinations and Vault (no arbitrary outbound)
- Admin endpoints (`/admin/*`): restricted to cluster-internal callers only
- WireGuard node encryption enabled (Cilium feature, zero config)
- Hubble observability for audit of network flows

For the hosted Pro service: Cilium is the CNI on Linode LKE. For
Enterprise self-hosted: ship the `CiliumNetworkPolicy` manifests in the
Helm chart (no-op if customer uses a different CNI — standard
`NetworkPolicy` fallback included).

### SSO / SAML for admin UI

Enterprise customers will not put the admin UI behind a shared password.
They need it in their IdP (Okta, Entra ID, etc.). Required for any regulated
customer procurement.

### Vault integration

See Security hardening above. Vault sidecar is the primary delivery pattern.
Direct SDK integration (`hvac`) available as an alternative for customers not
running the Vault Agent Injector.

### SLA + dedicated support

Email / Slack, guaranteed response times (P1 ≤ 4h, P2 ≤ 1 business day).

### Audit log export

Ship the token audit log to the customer's SIEM: Splunk, Datadog, Elastic.
Delivered via webhook or direct integration (S3, Splunk HEC, Datadog Logs API).

### Self-hosted Pro features

Run Pro features (admin UI, audit log) on the customer's own infrastructure.
Common ask from regulated industries (FedRAMP, HIPAA). Delivered as a Helm
chart with an Enterprise license key (signed JWT validated at startup).

---

## Postgres / Helm notes

**Do not use `bitnami/postgresql`.** In 2024 Bitnami moved their maintained
images to a paid subscription (`registry.bitnami.com`). The free images on
Docker Hub are no longer updated for security. The chart is still open source
but it defaults to the stale images, which defeats the purpose.

### Pro tier — minimal custom subchart

Write a small subchart using the **official Docker Hub `postgres:16-alpine`
image**. Docker Official Images are free, maintained by the Docker team, and
Trivy scans catch CVEs before they reach the cluster. The mock-idp Pro use
case (single-replica, token audit log, org registry) does not need the
complexity of a full operator.

Subchart scope:
- `Deployment` or `StatefulSet` with `postgres:16-alpine`
- `PersistentVolumeClaim` for data directory
- `Secret` for credentials (generated on install)
- `Service` (ClusterIP)
- Init ConfigMap for schema (`alembic upgrade head` run as an init container
  from the mock-idp image)

This is ~200 lines of YAML and gives full control over the image, storage
class, and resource limits without a third-party dependency.

### Enterprise tier — CloudNativePG (CNPG)

For Enterprise HA (failover, S3 backups, point-in-time recovery), use the
**CloudNativePG operator** (`cnpg-io/cloudnative-pg`):

- CNCF sandbox project (2022), actively maintained
- Uses the official `ghcr.io/cloudnative-pg/postgresql` images (based on
  official Postgres, rebuilt regularly with CVE patches)
- Kubernetes-native CRD (`Cluster` resource) — no StatefulSet to manage
- Includes streaming replication, automated failover, scheduled backups to S3

```yaml
# values.yaml — Enterprise installs opt into CNPG
cnpg:
  enabled: true
  instances: 3
  storage:
    size: 10Gi
```

CNPG is the right path for any Enterprise customer that asks for HA or needs
point-in-time recovery for the audit log.

---

## Build sequence

Prioritized by time-to-revenue:

1. **In-app rate limiting** (OSS, v0.6.0) — self-protection, readiness probe integration
2. **Hosted endpoint** (Pro, v0.7.0) — routing layer + slug registry + billing gate
3. **Token audit log** (Pro, v0.6.0) — differentiates Pro from Community immediately
4. **Web admin UI** (Pro/Enterprise, v0.8.0) — depends on Postgres backend (v0.4.0 already shipped)
5. **Cilium Gateway API rate limiting** (Pro, v0.7.0) — per-org HTTPRoute limits at the eBPF layer
6. **Webhook reliability** — pull forward when a customer asks
7. **SSO/SAML** — required for first Enterprise sale
8. **Vault sidecar integration** — required for regulated-industry Enterprise sale
9. **CIS hardened image** — required for Enterprise procurement in regulated industries
10. **FIPS 140-2 mode** — required for FedRAMP / DoD customers
11. **Audit log export** — upsell / expansion within Enterprise accounts

---

## Infrastructure

- **Hosted endpoint:** Linode LKE (Akamai Cloud managed Kubernetes).
  Start: 1 worker node + free managed control plane. Scale by adding nodes.
- **CNI + Gateway: Cilium** — single control plane for networking, L7 policy,
  and ingress. Cilium Gateway API implementation replaces a separate ingress
  controller. `GatewayClass` → `Gateway` → per-org `HTTPRoute`. WireGuard
  node-to-node encryption and Hubble observability included at zero extra cost.
- **Why Kubernetes Gateway API over nginx Ingress:** nginx's governance
  deteriorated after F5's acquisition (core developer forked to `freenginx`
  in 2024). The Kubernetes `Ingress` API is in maintenance mode — Gateway API
  is the forward standard from SIG-Network. Cilium's native Gateway API
  implementation means no separate ingress controller process at all.
- **CI/CD self-hosted runner:** Proxmox lab → k3s cluster → GitHub Actions
  self-hosted runner. Planned; see roadmap.
- **Postgres:** `postgres:16-alpine` (custom subchart for Pro); CloudNativePG
  operator for Enterprise HA. See [Postgres / Helm notes](#postgres--helm-notes) above.

---

## Base Image Policy

Container base image decisions follow the same governance principle as all
other technology choices: prefer community-governed projects with credible
maintainer lineage; avoid images controlled by vendors with a track record
of hostile or erratic stewardship.

| Build | Base image | Rationale |
|---|---|---|
| **Community** | `python:3.x-slim-bookworm` (Debian) | Familiar, predictable, well-supported. Debian is the canonical community-governed Linux — no corporate parent, 30-year track record. |
| **Pro** | `python:3.x-alpine` | Smaller attack surface, musl libc, minimal CVE surface. Test for C-extension compatibility. Trivy-scanned on every build. |
| **Enterprise / FIPS** | `cgr.dev/chainguard/python` | Daily rebuilds, cosign-signed via sigstore, near-zero CVEs, distroless-adjacent. Chainguard was founded by Dan Lorenc (co-creator of sigstore/cosign) — the team that wrote the supply chain security tooling everyone else uses. Strongest lineage for regulated customers. |

**Images to avoid:**

- **Bitnami** — documented above; stale public images since 2024 paid tier split.
- **Oracle Linux / UBI Oracle** — Oracle has a long history of hostile behavior
  toward open source (Java, MySQL, OpenOffice, RHEL clone lawsuit). Keep Oracle
  out of the stack on governance principle.
- **Ubuntu in containers** — Canonical's snap push and telemetry decisions have
  eroded trust. Debian slim is the same thing without the Canonical baggage.

**On Chainguard specifically:** their images are rebuilt and re-signed daily.
The `cgr.dev/chainguard/python` image is the right choice for the Enterprise
FIPS-hardened tier — regulated customers in procurement conversations will ask
"how do you handle base image CVEs?" and "how do you sign your images?" Chainguard
answers both out of the box, and the sigstore signing chain is auditable.

**On Alpine for Pro:** Alpine uses musl libc instead of glibc. Pure Python
packages are fine. Compiled C extensions (e.g., `asyncpg`, `cryptography`) need
testing — the `cryptography` wheel ships a bundled OpenSSL so it works, but
verify any new compiled dependency against Alpine before adding it.
