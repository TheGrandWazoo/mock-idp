# Business Model — Internal Reference

> **Internal only. Do not publish or include in public releases.**

---

## License

The public codebase is released under **Elastic License v2 (ELv2)**:

- Free for internal use, self-hosting, and development.
- Third parties **cannot** host mock-idp as a managed service without a commercial license.
- Source is visible but not "open source" in the OSI sense — ELv2 is source-available.

ELv2 can be changed in future versions if the strategy shifts (e.g., to a stricter proprietary license or to Apache 2.0 if we decide to go fully open). It only applies forward — existing releases stay under the version they shipped with.

---

## Open-Core Model

The repo is split into two layers:

| Layer | Repo | Visibility | License |
|-------|------|------------|---------|
| Core | `mock-idp` (this repo) | Public | ELv2 |
| Enterprise | `mock-idp-enterprise` | Private | Proprietary |

Enterprise features ship as a separate Python package that extends the core via FastAPI routers and plugin hooks. A license key (signed JWT) is validated at startup; no phone-home required — fully offline-capable.

---

## Pricing Tiers (Starting Points)

These are starting points, not commitments. Raise prices once there is demonstrated demand.

| Tier | Monthly Price | Notes |
|------|--------------|-------|
| **Community** | Free | ELv2 core, self-hosted |
| **Pro** | $20–40/mo per team | Secret backends, audit log, CLI, GitHub Action |
| **Enterprise** | $100–200/mo | All Pro + CyberArk, LDAP/AD, HSM/FIPS, SAML, SLA, Terraform provider |
| **Hosted** | $15–30/mo per workspace | We operate it; ELv2 protects this revenue stream |

**Do not charge until people are actively asking to pay.** Target milestone: 500+ GitHub stars or 3+ inbound "can I pay for X?" requests.

---

## Planned Premium Features

### Secret Backends (Pro)
- HashiCorp Vault (`hvac`)
- AWS Secrets Manager (`boto3`)
- Azure Key Vault (`azure-keyvault-secrets`)
- GCP Secret Manager (`google-cloud-secret-manager`)
- Environment variable and file-based secrets (replaces plaintext in config)

### Enterprise Identity
- LDAP / Active Directory user sync
- SAML 2.0 SP metadata for IdP federation

### Per-Audience Roles and Groups (Pro)
- Users and clients can present different roles/groups per target audience
- e.g., alice gets `[operator, responder]` for `api://serviceB` but `[admin]` for `api://serviceC`
- Eliminates the need to create duplicate identities just to vary claims per service

### Advanced Protocols
- Authorization Code + PKCE flow
- DPoP (Demonstration of Proof-of-Possession)
- Pushed Authorization Requests (PAR)
- mTLS client authentication

### Compliance
- Structured audit log (token issuance, admin actions) — JSON, optional SIEM sink
- HSM signing key support (PKCS#11)
- FIPS 140-2 mode

### Developer Tooling
- GitHub Actions action (`mock-idp-action`) for ephemeral test IdPs in CI
- Terraform provider
- CLI (`mock-idp-ctl`) for runtime identity/token management
- VS Code extension (token playground in-editor)

### Hosted Service
- Managed instances (we operate, customer configures)
- Multi-workspace isolation
- Built-in observability (Prometheus metrics, health dashboard)

---

## License Enforcement Reality

License checks in open-core are **social contracts for honest customers**, not hard technical barriers. A determined actor can:

- Patch out the validation check
- Extract the enterprise package if they obtain it

Mitigations used:
- Enterprise package stays in a **private repo** — they need to steal code, not just patch a check.
- Light obfuscation of the license validation module (PyArmor or Cython-compiled).

Accept that bad actors who bypass the check were not going to pay anyway. Focus enforcement energy on making the legitimate path easy and the legal risk of bypassing it clear in the license terms.

---

## Near-Term Priority

**Vault + AWS SM integration (Pro tier)** — this is the first feature teams hit when mock-idp moves from local dev to shared test environments, and the most common reason they'd pay. Implement this first.

See [roadmap.md](roadmap.md) for the implementation roadmap including vault integration as a v0.3 candidate.
