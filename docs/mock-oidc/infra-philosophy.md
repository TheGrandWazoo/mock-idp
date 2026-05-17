# Infrastructure Philosophy — Technology Choices & Governance

> **Internal only. Do not publish.**
> Last updated: 2026-05-17

This document captures the reasoning behind our infrastructure technology
choices. The goal is not to re-litigate decisions every sprint — it is to
record *why* so that future choices stay consistent and anyone joining the
project understands the principles before touching the stack.

---

## The Governing Principle

**Follow the original developer when a project forks due to governance
or ownership breakdown.**

When a corporate acquisition or governance failure causes the people who
*built* a project to leave it, that exit is the strongest possible signal
about the project's future trajectory. The original developer knows the
code, the design intent, and the community better than anyone. If they
leave — especially to fork — they are telling you something important.

This is not ideology. It is pattern recognition from repeated observed
outcomes:

| Original project | Governance event | Fork / alternative | Signal |
|---|---|---|---|
| m0n0wall | Manuel Kasper ended the project | OPNsense (Kasper endorsed it before shutting down m0n0wall) | Kasper's endorsement is the strongest possible signal |
| pfSense | Sold to Netgate; community/direction conflicts | OPNsense | Same fork — now the clearly governed alternative |
| CentOS | Red Hat (IBM) killed CentOS 8, moved to Stream | Rocky Linux (Gregory Kurtzer, CentOS co-founder) | Founder created the replacement within days |
| nginx | F5 acquisition; F5 security team bypassed nginx developers on CVE | freenginx (Maxim Dounin, core developer for 18 years) | Core developer left over integrity of the security process |
| OpenOffice | Oracle acquisition | LibreOffice (Document Foundation, original developers) | Oracle has a consistent track record of hostile OSS stewardship |

The counterpoint: not all forks succeed. A fork is necessary but not
sufficient — it also needs community, momentum, and continued quality.
Rocky Linux has all three. freenginx is smaller but technically credible.
Both are worth using over the originals.

---

## OPNsense

**Use OPNsense. Do not use pfSense.**

OPNsense is the firewall/router platform for lab and edge deployments in
this project. The choice follows the principle above:

- **m0n0wall** (Manuel Kasper) was the original BSD-based embedded firewall.
  Kasper shut it down in 2015 and explicitly endorsed OPNsense as the
  successor — he called it "the better choice." That is not a typical OSS
  handoff; it is the original author vouching for the fork that honors the
  original design intent.
- **pfSense** was itself a fork of m0n0wall, but has drifted under Netgate
  ownership. Netgate has made business decisions that prioritized revenue
  over community trust (proprietary hardware bundles, licensing changes).
- **OPNsense** (Deciso B.V., Dutch company) is actively developed, has a
  clear governance structure, frequent security releases, and a cleaner
  plugin ecosystem. Deciso is a commercial entity but they have consistently
  demonstrated that the project's integrity matters more than short-term
  revenue extraction.

**Relevance to this project:** the Proxmox lab that will run the k3s
CI/CD runner and pre-prod environments sits behind OPNsense. Network
policy, VLAN isolation, and certificate management for the lab all flow
through it.

---

## Linux Distributions

### Rocky Linux — preferred for RHEL-compatible workloads

Gregory Kurtzer co-founded CentOS. When Red Hat/IBM ended CentOS 8 in
December 2020, Kurtzer announced Rocky Linux within 48 hours and launched
the Rocky Enterprise Software Foundation (RESF) — a community foundation,
not a vendor. That governance structure matters: RESF is not owned by a
company that can pivot away from the community.

Rocky is the right choice for any workload that needs RHEL binary
compatibility (enterprise software certifications, support contracts,
consistent RPM ecosystem).

**AlmaLinux** is the other major CentOS replacement, backed by CloudLinux
(a company). It is technically sound. Rocky is preferred here because RESF's
governance model is closer to the community-foundation standard we trust.

### Debian — preferred for everything else

Community-governed since 1993. No corporate parent. No vendor that can
decide to monetize the project in a direction the community would not choose.
The Debian Social Contract and DFSG are the governance documents — they have
held for 30 years.

Debian is the base for Ubuntu, Raspberry Pi OS, Kali, and hundreds of others.
When in doubt, Debian is the answer.

### Ubuntu — use with awareness

Ubuntu is Debian-derived and technically excellent. Canonical has made
decisions that eroded trust: forcing snap packages as the default for some
core tools (firefox, chromium), telemetry collection that required opt-out,
and proprietary tooling (Landscape, Ubuntu Advantage) being pushed into the
free tier. None of these are disqualifying for all use cases, but they
represent the kind of drift that precedes larger governance problems.
Prefer Debian slim over Ubuntu in containers. On the server, Rocky or
Debian is the cleaner choice.

### Distros to avoid

- **Oracle Linux** — Oracle has a long, well-documented history of hostile
  behavior toward open source projects they do not control: Java (GPL
  lawsuit against Google), MySQL (MariaDB fork exists for a reason),
  OpenOffice (community moved to LibreOffice), and their lawsuit against
  open-source RHEL rebuilders. Keep Oracle out of the stack entirely.
- **RHEL itself** (directly) — requires subscription, and IBM/Red Hat's
  2023 decision to restrict CentOS rebuild access confirmed that they will
  use RHEL's position to extract revenue from the community. Rocky is the
  correct free alternative.

---

## Container Base Images

Container base image decisions follow the same governance principle:
prefer images maintained by community-governed projects or teams with
credible, verifiable supply chain security lineage.

### Community builds — `python:slim-bookworm` (Debian)

The official Docker Library Python images based on Debian Bookworm slim.
Docker Official Images are maintained by the Docker community and the
upstream language teams. Debian Bookworm is the current stable release.

Predictable, well-supported, broad ecosystem compatibility. The right
choice when simplicity and familiarity matter more than minimal footprint.

### Pro builds — `python:alpine`

Alpine Linux is community-governed, musl libc-based, and minimal by design.
The attack surface is significantly smaller than Debian slim. Trivy
typically finds fewer CVEs on Alpine images.

**The musl caveat:** Alpine uses musl libc instead of glibc. Most pure
Python code works transparently. Compiled C extensions need testing:
- `cryptography` — ships a bundled OpenSSL wheel; works on Alpine
- `asyncpg` — compiles against musl; works but test explicitly
- `psycopg2` — use `psycopg2-binary` or compile with musl headers
- Any extension with glibc-specific assumptions — test before deploying

When adding a new compiled dependency, test against the Alpine image in
CI before merging.

### Enterprise / FIPS builds — `cgr.dev/chainguard/python`

**Chainguard Images** are the right choice for regulated customers.

Dan Lorenc founded Chainguard after co-creating sigstore and cosign at
Google. He is the person who wrote the tooling that everyone else uses
to sign container images. The supply chain lineage is as strong as it
gets in the container security space.

What Chainguard images provide:
- **Daily rebuilds** — CVEs are patched within hours, not weeks
- **cosign-signed** — every image has a verifiable sigstore signature;
  customers can verify the supply chain cryptographically
- **Distroless-adjacent** — no shell, no package manager, minimal
  attack surface
- **Near-zero CVE baseline** — Trivy scans on Chainguard Python images
  consistently show zero or near-zero findings

Why this matters for Enterprise procurement: regulated industry customers
(FedRAMP, financial services, healthcare, DoD-adjacent) will ask two
questions in procurement: "How do you handle base image CVEs?" and "How
do you sign your images?" Chainguard answers both out of the box, and
the sigstore signing chain is auditable by the customer's own security team.

**Wolfi** is the underlying OS that Chainguard images are built on —
a purpose-built, musl-based container micro-distro created by the same
team. It is not a general-purpose server OS; it exists specifically to
be a minimal, secure container base.

### Images to avoid

**Bitnami** — In 2024 Bitnami moved their maintained images to a paid
subscription tier (`registry.bitnami.com`). The public Docker Hub images
are no longer receiving security updates. Already documented in
`pro-enterprise.md`. Do not use for any component.

**Oracle-anything** — See distros section above. Oracle Container Registry,
Oracle Linux base images, GraalVM Oracle distribution. Keep Oracle out of
the supply chain.

**Ubuntu in containers** — Functionally similar to Debian slim but with
Canonical overhead. Debian slim is the same image without the corporate
governance risk.

---

## freenginx — Assessment

**Verdict: worth using in lab/traditional server contexts; superseded by
Cilium Gateway API for Kubernetes ingress.**

### Background

Maxim Dounin was a core nginx developer for 18 years. In January 2024 he
created freenginx after a dispute with F5: F5's security team applied a
security fix to the nginx codebase without going through the nginx development
team's review process. From Dounin's perspective, F5 used their corporate
ownership to bypass the engineering process that ensures code quality and
correctness. He left and forked the project under the freenginx name.

This follows the exact governance pattern that produced OPNsense and Rocky
Linux. The person with the deepest knowledge of the codebase left over a
principled disagreement about process integrity.

### Honest assessment

**Strengths:**
- Dounin is technically credible — he has 18 years of nginx internals
  knowledge and is a serious engineer
- freenginx is a direct fork, not a rewrite — the codebase is mature
- Governance is clear: community-driven, no F5 influence
- Actively maintained as of this writing

**Weaknesses:**
- Small team — primarily Dounin, not an organization with a security
  response team, a bug bounty program, or a marketing budget
- Ecosystem tooling (certbot nginx plugin, nginx Ingress Controller,
  commercial WAF rules) is built against nginx, not freenginx
- No LTS release cadence announced yet
- Production deployments at scale are not yet widely documented

### Where freenginx fits for this project

For **Kubernetes ingress**, the nginx question is superseded entirely.
Kubernetes Gateway API with Cilium's native implementation is the forward
standard. We are not running nginx Ingress in any tier of this project —
not community, not Pro, not Enterprise. freenginx vs nginx is irrelevant
to the Kubernetes stack.

For **Proxmox lab / traditional server use** (reverse proxy, cert
termination on bare metal or VMs): freenginx is worth using over nginx.
You get the same codebase, the governance you trust, and the original
author's continued attention. The ecosystem gap (certbot plugin etc.) is
manageable in a lab context.

**Watch signal:** if freenginx gains a formal foundation structure, an
LTS release, or a security advisory process — the case for production use
outside Kubernetes strengthens significantly. Track it at
[freenginx.org](https://freenginx.org).

---

## Cilium + Kubernetes Gateway API

**Use Cilium as the CNI. Use Cilium's native Gateway API implementation
for ingress. No separate ingress controller.**

This is documented in detail in `pro-enterprise.md` and `business-model.md`.
The summary:

- Cilium is the CNI on Linode LKE (hosted Pro) and k3s (Proxmox lab)
- Cilium Gateway API replaces a separate nginx/Kong/Traefik ingress controller
- One control plane: networking + L7 network policy + ingress routing
- WireGuard node encryption and Hubble network observability are included
  and zero-configuration
- `CiliumNetworkPolicy` CRDs provide L7 HTTP path-level policy
  (restrict `/admin/*` at the network layer, not just app layer)
- Standard `NetworkPolicy` fallback shipped in Enterprise Helm chart
  for customers on non-Cilium CNIs (Calico, AWS VPC CNI, etc.)

The Kubernetes `Ingress` API is in maintenance mode — no new features,
only bug fixes. Gateway API (`HTTPRoute`, `GRPCRoute`, `TLSRoute`) is
the forward standard from Kubernetes SIG-Network. Any new deployment
in 2026+ should start with Gateway API.

---

## Summary — Approved Stack by Layer

| Layer | Choice | Avoid |
|---|---|---|
| Firewall / router | OPNsense | pfSense (Netgate governance) |
| Server OS | Rocky Linux, Debian | Oracle Linux, Ubuntu Server (preference) |
| Container base (community) | `python:slim-bookworm` | Ubuntu, Bitnami, Oracle |
| Container base (Pro) | `python:alpine` | — |
| Container base (Enterprise) | `cgr.dev/chainguard/python` | — |
| CNI | Cilium | Flannel (no L7), Calico (acceptable fallback only) |
| Kubernetes ingress | Cilium Gateway API | nginx Ingress (legacy API), Kong (premature complexity) |
| Traditional proxy (lab) | freenginx | nginx (F5 governance) |
| Postgres (Pro) | `postgres:16-alpine` custom subchart | Bitnami postgresql |
| Postgres (Enterprise) | CloudNativePG operator | Bitnami, Oracle |

---

## References

- [business-model.md](business-model.md) — pricing, market position, infrastructure plan
- [pro-enterprise.md](pro-enterprise.md) — build sequence, Cilium/Gateway API detail, base image policy
- [roadmap.md](roadmap.md) — milestone feature list
- [freenginx.org](https://freenginx.org) — watch for LTS / foundation announcement
- [Chainguard Images](https://edu.chainguard.dev) — base image reference
- [Kubernetes Gateway API](https://gateway-api.sigs.k8s.io) — SIG-Network forward standard
- [Cilium Gateway API docs](https://docs.cilium.io/en/stable/network/servicemesh/gateway-api/) — implementation reference
