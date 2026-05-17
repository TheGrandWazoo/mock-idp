# Business Model — Internal Reference

> **Internal only. Do not publish or include in public releases.**
> Last updated: 2026-05-17

---

## License

The public codebase is released under **MIT**:

- Free to use, modify, distribute, and self-host — no restrictions
- MIT is the distribution engine: low friction drives adoption and GitHub stars
- The hosted SaaS service is the revenue moat, not the license

**Why MIT over ELv2:** ELv2 prevents third-party hosted services but adds friction
for the community adoption that drives the top of the funnel. At this stage, star
count and word-of-mouth matter more than protecting hosting revenue. If a well-funded
competitor launches a hosted version, revisit for future releases — existing releases
always keep the license they shipped with.

---

## Open-Core Model

| Tier | Repo | Visibility | License | Price |
|---|---|---|---|---|
| **Community** | `mock-idp` | Public | MIT | Free |
| **Pro** | `mock-idp` + hosted infra | Public core | Proprietary SaaS | $29–49/mo per org |
| **Enterprise** | `mock-idp-enterprise` | Private | Proprietary | $150–300/mo |

**Repo strategy:** All work lives in `mock-idp` until hosted service code diverges enough
to warrant a separate `mock-idp-cloud` private repo. Expected trigger: v0.7.0 (billing
integration, org management, multi-tenant routing). A GitHub Project tracks the
Pro/Enterprise epic across milestones.

Enterprise features ship as a separate Python package extending core via FastAPI routers
and plugin hooks. License key (signed JWT) validated at startup; fully offline-capable.

---

## Pricing Tiers

**Do not charge until 500+ GitHub stars or 3+ inbound "can I pay for X?" requests.**
Validate with a waitlist or 30-day free trial cohort first.

### Community — Free
- MIT, self-hosted, Docker image + Helm chart
- Full OIDC feature set: all grant types, JWKS, token exchange (RFC 8693),
  realm roles, webhooks, playground
- YAML config store

### Pro — $29–49/month per org
- Hosted endpoint: `mock.ksatechnologies.com/{org}/{issuer}/token`
- Zero-setup CI — no sidecar, no Docker required in CI pipelines
- Per-org Postgres-backed config (push config via admin API)
- Token audit log (every `/token` call stored, queryable via UI + API)
- Web admin UI for identity and org management
- GitHub Actions marketplace action
- Multi-provider claim shapes (Okta, Cognito, Keycloak, Entra ID)
- Error/chaos injection admin API (test auth failure paths in CI)
- Priority support — email, 48h response SLA

### Enterprise — $150–300/month (annual contract, negotiated)
- Everything in Pro, self-hosted on customer infrastructure
- CloudNativePG for HA Postgres
- HashiCorp Vault / AWS Secrets Manager / Azure Key Vault integration
- LDAP / Active Directory user sync
- SAML 2.0 SP federation
- mTLS / certificate-bound tokens (RFC 8705)
- Authorization Code + PKCE flow
- Device Authorization Grant (RFC 8628)
- HSM signing key support (PKCS#11)
- FIPS 140-2 mode
- Terraform provider + Kubernetes CRD operator
- SSO for admin UI
- SLA (99.9% uptime; 4h support response for self-hosted)
- Dedicated Slack channel

---

## Revenue Math

At $39/month (midpoint Pro pricing):

| Target | Paying orgs needed |
|---|---|
| $5k/month | 128 orgs |
| $10k/month | 257 orgs |
| $50k/month | 1,282 orgs |

OSS-to-paid conversion: 1–5% typical. At 1%, need 12,800 community users for
$50k/month. **Realistic near-term target:** $5k/month within 12–18 months of
hosted launch, assuming 1,000+ GitHub stars and active GHA marketplace action.

**Milestones:**
- $5k/month → sustainable solo operation
- $10k/month → hire part-time US-based help desk / contractor support

---

## Market Position

### The Gap
No well-known, purpose-built hosted mock OIDC SaaS exists at the $29–49/month price
point (as of 2026-05-17 research). The gap is real and validated.

### Primary Differentiators

| Differentiator | Why it matters |
|---|---|
| **Entra ID / Azure AD claim fidelity** | `oid`, `tid`, `scp`, `roles`, correct `iss` format. Nothing else does this well. Strongest differentiator. |
| **Persistent org-scoped JWKS URL** | Stable across CI job isolation, no sidecar. Solves the #1 CI pain point. |
| **Token Exchange (RFC 8693)** | Rare in mock tools; real demand in service mesh / zero-trust testing. |
| **On-Behalf-Of (OBO) flow** | Entra-specific chain threading — no competitor handles correctly. |
| **Zero-setup hosted** | vs. Keycloak (2–3 min startup, 512 MB, complex config). |
| **Multi-provider shapes** | Okta, Cognito, Keycloak, Ping shapes via config — unique in Python ecosystem. |

### Competitive Landscape

| Competitor | Gap |
|---|---|
| Keycloak | Heavy, self-hosted only, not a mock |
| WireMock Cloud | HTTP mock, not OIDC-aware, no JWT signing |
| Microcks | OpenAPI mock, hosted in beta, not Entra-shaped |
| navikt/mock-oauth2-server | Popular OSS, no hosted, not Entra-shaped |
| Auth0 / Okta dev tenants | Real IdPs, rate-limited, network-dependent |
| axa-group/oauth2-mock-server | npm only, incomplete PKCE, no hosted |

### Target Customers
- Azure-heavy shops (M365, Teams, Azure) where Entra ID token fidelity is required
- Platform / DevEx teams maintaining shared CI infrastructure
- API gateway teams (Kong, AWS API GW, Azure APIM, Apigee) writing integration tests
- Service mesh teams (Istio, Linkerd) testing OIDC policies
- Companies with on-premises AD synced to Entra ID via AD Connect

---

## Complementary Products (Integration Ecosystem)

mock-idp is a **testing supplement**, not a replacement for real IdPs. It sits
alongside these products in CI/CD and dev environments:

| Ecosystem | Role | Discovery path |
|---|---|---|
| Kong, AWS API GW, Azure APIM, Apigee | Token validation target — mock-idp issues the tokens they validate | Kong Hub listing, docs integration guides |
| Istio / Linkerd | OIDC JWT policy testing | CNCF ecosystem, Helm Hub |
| GitHub Actions | CI token generation | GHA Marketplace action (primary growth lever) |
| Azure DevOps, GitLab CI | Same CI use case | Marketplace equivalents |
| Playwright / Cypress / k6 | Auth setup for E2E and load tests | npm/PyPI install docs |
| Kubernetes OIDC webhook / Dex / Pinniped | Platform auth testing | Helm Hub, CNCF community |
| Backstage / Port.io | Developer portal token playground integration | Plugin ecosystem |
| Terraform | IaC-managed test identities | Terraform Registry (future provider) |

**Key insight:** mock-idp is discovered *through* the products it tests. The GHA
marketplace action, a Kong Hub listing, and a Helm Hub chart are the three highest-ROI
distribution channels.

---

## Active Directory / Entra ID Context

**AD ≠ Entra ID.** Clarification for positioning:

- **Active Directory (on-prem)** — LDAP/Kerberos directory, not OIDC-native
- **Entra ID** (formerly Azure AD) — Microsoft's cloud IdP, OIDC/OAuth2 native, included
  with M365 and Azure subscriptions (Free tier). P1/P2 add conditional access, PIM, etc.
- **Entra External ID** — B2C/B2B federation for external users

Companies with on-prem AD typically sync to Entra ID via **AD Connect** or **Entra Connect**.
Their apps authenticate against Entra ID, not AD directly. mock-idp targets the **Entra ID
token shape** — so it supplements testing for any company in the Microsoft cloud ecosystem,
whether their users come from on-prem AD or cloud-only Entra ID.

mock-idp does NOT replace Entra ID in production. It replaces the dependency on Entra ID
in **CI/CD pipelines and local development** where a live Entra ID tenant would require
network access, app registrations, and managed credentials.

---

## Infrastructure Plan

### Dev / Lab
- K3s on Proxmox (local lab) — integration testing, pre-prod smoke

### Production
- **Linode LKE** (Akamai Cloud managed Kubernetes)
  - Start: 1 worker node (~$24/month 4GB) + free managed control plane
  - Scale: add nodes as load grows, no re-architecture
  - Migration path to multi-region when revenue justifies it
- **Postgres:** CloudNativePG operator — start single-node, promote to HA at ~50 paying orgs

---

## Image Distribution (Pro/Enterprise)

```
Community:   ghcr.io/thegrandwazoo/mock-idp         (public, MIT)
Pro:         ghcr.io/thegrandwazoo/mock-idp-pro      (private GHCR)
Enterprise:  Helm chart + Kubernetes image pull secret
```

Customer access: GitHub PAT scoped to `read:packages` only — the PAT **is** the license.
Revoking it on subscription cancellation terminates access. Automation: Stripe webhook →
GitHub API creates fine-grained PAT → email to customer.

---

## Payment Processing

**Primary: Stripe** — industry standard, subscriptions, invoicing, card storage.
Start manual (Stripe payment link → manually provision GHCR PAT + Postgres org record).

**Alternative: Lemon Squeezy** (acquired by Stripe, March 2024) — merchant of record,
handles global VAT/tax compliance automatically. Worth switching if international
customers become significant.

---

## Feature Roadmap Priorities

Based on market research (2026-05-17), ranked by signal strength × implementation effort:

> Token introspection (RFC 7662) shipped in v0.4.1.

| Priority | Feature | Milestone | Complexity |
|---|---|---|---|
| 1 | Token audit log + multi-org schema | v0.6.0 | Medium |
| 2 | Hosted endpoint (slug routing) | v0.7.0 | Medium |
| 3 | Prometheus /metrics endpoint | v0.6.0 | Small |
| 4 | Multi-provider shapes (Okta/Cognito/Keycloak) | v0.6.0–v0.7.0 | Medium |
| 5 | GitHub Actions marketplace action | v0.7.0 | Small |
| 6 | Error/chaos injection admin API | v0.7.0 | Medium |
| 7 | OBO flow (Entra-specific) | v0.7.0 | Small-Med |
| 8 | Dev Container feature | v0.7.0 | Small |
| 9 | PKCE / Authorization Code flow | v0.8.0+ | Medium |
| 10 | Web admin UI | v0.8.0 | Large |
| 11 | Device Authorization Grant (RFC 8628) | v0.8.0+ | Medium |
| 12 | mTLS / cert-bound tokens (RFC 8705) | Enterprise | Large |
| 13 | Terraform provider / CRD operator | Enterprise | Large |
| 14 | SAML 2.0 bridge | Backlog | Large |

---

## Growth Strategy

1. **OSS → stars → word of mouth** — MIT drives adoption; GitHub stars are the leading indicator
2. **GHA marketplace action** — free action is the highest-leverage growth lever; CI users
   are the natural Pro upsell
3. **Entra ID positioning** — target Azure-heavy shops where token fidelity is non-negotiable
4. **Waitlist before charging** — collect emails, validate demand before building billing infra
5. **US-based help desk** at ~$10k/month ARR — part-time contractor, async support

---

## License Enforcement Reality

License checks are social contracts for honest customers, not hard technical barriers.
Enterprise package stays in a private repo (requires code theft, not just a patch).
Bad actors who bypass were never going to pay — focus on making the legitimate path easy.

---

## References

- [roadmap.md](roadmap.md) — Implementation roadmap and version history
- [pro-enterprise.md](pro-enterprise.md) — Detailed Pro/Enterprise feature breakdown
- [ADR-000-process.md](ADR-000-process.md) — ADR lifecycle (RFC-style)
- GitHub Milestone #2 — v0.6.0 Hosted foundation
- GitHub Milestone #3 — v0.7.0 Hosted endpoint (Pro)
- GitHub Milestone #4 — v0.8.0 Web admin UI
- GitHub Project — Pro / Enterprise epic
