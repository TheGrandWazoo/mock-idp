# Pro / Enterprise — Strategy & Roadmap

mock-idp is MIT-licensed and self-hosted. Monetization works by selling the
*workflow and infrastructure around it*, not the tool itself. The tool stays
open source — that is the distribution engine.

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

---

## Pro

### 1. Hosted mock endpoint (highest priority)

> User decision: this is the first Pro feature to build.

A team gets a persistent subdomain:
`https://mock.ksatechnologies.com/{org-slug}/{issuer}/token`

**Why this first:**
- Zero setup — works from ephemeral CI runners without a sidecar container
- Eliminates the main friction point for new adopters
- Routing layer is a thin nginx/Caddy reverse-proxy in front of the existing image
- Billing surface: per-org slug gated behind an API key

**Implementation sketch:**
- Slug registry (Postgres): `org_slug → config_id`
- Provisioning API: `POST /api/orgs` → creates slug, returns API key
- Config upload: `PUT /api/orgs/{slug}/config` → stores YAML, triggers hot-reload
- Routing: nginx `proxy_pass` to a pool of mock-idp instances keyed by slug

**Postgres subchart:** Use `bitnami/postgresql` as a Helm chart dependency
(see [Postgres / Helm notes](#postgres--helm-notes) below). The Bitnami chart
is battle-tested and eliminates the need to build PV management, initContainers,
and secrets handling from scratch.

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

### 4. Webhook reliability

The v0.5.2 webhook delivery is fire-and-forget. Pro adds:
- Delivery retries with exponential backoff (3 attempts)
- Dead-letter queue for failed deliveries
- Replay-from-UI for debugging

**When to build:** after hosted endpoint ships and a customer asks for it.
Already in the parked roadmap (see [roadmap.md](roadmap.md)).

---

## Enterprise

### SSO / SAML for admin UI

Enterprise customers will not put the admin UI behind a shared password.
They need it in their IdP (Okta, Azure AD, etc.). Required for any regulated
customer procurement.

### Vault integration

Pull secrets from HashiCorp Vault at startup. Config references
`vault://secret/mock-idp/clients`. Already in the parked roadmap.
Enterprises won't put client secrets in ConfigMaps.

### SLA + dedicated support

Email / Slack, guaranteed response times (e.g., P1 ≤ 4h, P2 ≤ 1 business day).

### Audit log export

Ship the token audit log to the customer's SIEM: Splunk, Datadog, Elastic.
Delivered via webhook or direct integration (S3, Splunk HEC, Datadog Logs API).

### Self-hosted Pro features

Run Pro features (admin UI, audit log) on the customer's own infrastructure.
Common ask from regulated industries (FedRAMP, HIPAA). Delivered as a Helm
chart with an Enterprise license key.

---

## Postgres / Helm notes

The `bitnami/postgresql` chart is the recommended approach for both the hosted
endpoint and the self-hosted Pro tier. It provides:

- PersistentVolumeClaim management
- Init scripts via ConfigMap
- TLS support
- `bitnami/postgresql-ha` for high-availability (Enterprise tier)
- Automated credential generation via Kubernetes secrets

**Do not build a custom subchart from scratch.** Add it as a chart dependency:

```yaml
# chart/Chart.yaml
dependencies:
  - name: postgresql
    version: "16.x.x"
    repository: https://charts.bitnami.com/bitnami
    condition: postgresql.enabled
```

The Bitnami images are published to `registry.bitnami.com` and are updated
frequently. They are acceptable for production use; Trivy scans in CI catch
any OS-level CVEs before they reach the cluster.

Alternatively, override `postgresql.image` to use the official `postgres:16-alpine`
image if image provenance is a concern — the Bitnami chart supports image overrides.

---

## Build sequence

Prioritized by time-to-revenue:

1. **Hosted endpoint** — routing layer + slug registry + billing gate
2. **Token audit log** — differentiates Pro from Community immediately
3. **Web admin UI** — depends on Postgres backend (v0.4.0 already shipped)
4. **Webhook reliability** — pull forward when a customer asks
5. **SSO/SAML** — required for first Enterprise sale
6. **Vault integration** — required for regulated-industry Enterprise sale
7. **Audit log export** — upsell / expansion within Enterprise accounts

---

## Infrastructure

- **Hosted endpoint:** Single Kubernetes cluster (Rancher/k3s or EKS/GKE).
  Nginx ingress routes `{org-slug}.*` to the right mock-idp pod pool.
- **CI/CD self-hosted runner:** Proxmox lab → k3s cluster → GitHub Actions
  self-hosted runner. Planned; see roadmap.
- **Postgres:** `bitnami/postgresql` Helm chart (see above).
