# Hosting PoC — mock-idp Deployment Options

**Status:** Planning — not yet executed
**Date:** 2026-05-15
**Goal:** Evaluate hosting platforms for delivering mock-idp to clients as a managed
test fixture. Identify the best balance of control, cost, operational simplicity, and
CI/CD integration given that the project already has a Dockerfile, Helm chart, and
GitHub Actions pipeline.

---

## Non-starters (ruled out before PoC)

| Platform | Reason excluded |
|---|---|
| AWS EKS / Google GKE / Azure AKS | Control-plane costs, IAM complexity, and vendor lock-in exceed the value for a test fixture. Wrong tool for this scope. |
| Railway | No Kubernetes support. Excellent GitHub integration and DX, but you lose the Helm chart story entirely. Included in comparison section below for completeness. |
| Render | Same as Railway — PaaS abstraction, no k8s. |
| Bare metal | No persistent state, no heavy compute requirement — owning hardware is waste. |

> **Note on Railway/Render:** These platforms are genuinely good for stateless containerised
> apps and have the best GitHub push-to-deploy experience available. If a future use case
> drops the Helm chart requirement and prioritises zero-ops above all else, revisit.
> The PoC section below includes a Railway trial so the comparison is data-driven, not assumed.

---

## Options under evaluation

### 1. DOKS — DigitalOcean Kubernetes Service

**Cost:** ~$12/mo per worker node (2 vCPU / 2 GB). Control plane is free.
**K8s:** Fully managed, CNCF-conformant. kubectl + Helm work as-is.
**GitHub integration:** GitHub Actions → `helm upgrade` via kubeconfig secret.

**Pros**
- Existing DO account; billing already set up
- Managed control plane — no etcd, no upgrade headaches
- DO load balancer integrates cleanly with k8s Services
- Good dashboard for quick visibility
- doctl CLI is solid

**Cons**
- $12/mo minimum per node — overkill for one small container
- Fewer regions than AWS/GCP
- Node autoscaler exists but less mature than EKS

**PoC steps**
1. `doctl kubernetes cluster create mock-idp-poc --region nyc1 --size s-2vcpu-2gb --count 1`
2. `doctl kubernetes cluster kubeconfig save mock-idp-poc`
3. `helm upgrade --install mock-idp ./chart -f chart/values.yaml`
4. Verify: `kubectl get pods`, hit `/healthz`, issue a token from the playground
5. Add `DOKS_KUBECONFIG` secret to GitHub repo; add deploy step to `ci.yml` (see §CI/CD below)
6. Push a change; confirm end-to-end automated deploy

**Verdict placeholder:** _Fill in after PoC_

---

### 2. Civo

**Cost:** ~$5/mo per node (1 vCPU / 2 GB). Control plane is free.
**K8s:** Managed k3s. Lightweight, fast (clusters spin up in ~90 seconds).
**GitHub integration:** GitHub Actions → `helm upgrade` via kubeconfig secret (same pattern as DOKS).

**Pros**
- Cheapest managed k8s available (~half the price of DOKS/LKE at entry level)
- k3s is production-grade and CNCF-conformant for most workloads
- Excellent developer experience — focused purely on k8s, no distractions
- Marketplace one-click installs (cert-manager, Traefik, etc.)
- Fast cluster provisioning

**Cons**
- Fewer regions (NYC, LON, FRA, PHX)
- Smaller ecosystem / less community content than DOKS or LKE
- k3s differences from full k8s are rarely a problem but worth knowing
- Less name recognition when presenting to clients who care about logos

**PoC steps**
1. Sign up at civo.com; install `civo` CLI
2. `civo kubernetes create mock-idp-poc --size g4s.kube.small --nodes 1 --wait`
3. `civo kubernetes config mock-idp-poc --save`
4. `helm upgrade --install mock-idp ./chart -f chart/values.yaml`
5. Verify and test same as DOKS step 4
6. Add `CIVO_KUBECONFIG` secret; wire deploy step; push and confirm

**Verdict placeholder:** _Fill in after PoC_

---

### 3. LKE — Linode / Akamai Kubernetes Engine

**Cost:** ~$12/mo per node (2 vCPU / 4 GB — better specs than DOKS at same price).
**K8s:** Fully managed, CNCF-conformant.
**GitHub integration:** Same kubeconfig-secret pattern.

**Pros**
- Better RAM-per-dollar than DOKS at entry level (4 GB vs 2 GB for ~same price)
- Akamai CDN integration if edge delivery ever becomes relevant
- Linode has a long track record; solid reputation for reliability
- NodeBalancers (load balancers) are straightforward

**Cons**
- Dashboard is functional but less polished than DO
- `linode-cli` is less refined than `doctl`
- No pre-existing account — setup cost (billing, API keys, etc.)

**PoC steps**
1. Sign up at linode.com; install `linode-cli`
2. `linode-cli lke cluster-create --label mock-idp-poc --region us-east --k8s_version 1.31 --node_pools.type g6-standard-2 --node_pools.count 1`
3. Download kubeconfig from Linode dashboard or CLI
4. `helm upgrade --install mock-idp ./chart -f chart/values.yaml`
5. Verify and test
6. Wire GitHub Actions; push and confirm

**Verdict placeholder:** _Fill in after PoC_

---

### 4. k3s on a Droplet (DIY)

**Cost:** $6/mo (1 vCPU / 1 GB Droplet) or $12/mo (2 vCPU / 2 GB).
**K8s:** Self-managed k3s — you own upgrades, certificates, and control plane.
**GitHub integration:** SSH deploy via GitHub Actions or kubeconfig over SSH tunnel.

**Pros**
- Cheapest real-k8s option available
- Full control — nothing is abstracted away
- Good learning path if the goal is understanding k8s internals
- Uses existing DO account

**Cons**
- You own the control plane — upgrades, cert rotation, etcd backup are your problem
- Single point of failure (no HA without multiple nodes)
- More setup time than managed options
- Not appropriate for presenting to clients as "managed infrastructure"

**PoC steps**
1. `doctl compute droplet create mock-idp-k3s --size s-2vcpu-2gb --region nyc1 --image ubuntu-24-04-x64`
2. SSH in; `curl -sfL https://get.k3s.io | sh -`
3. Copy `/etc/rancher/k3s/k3s.yaml` locally; update server address
4. `helm upgrade --install mock-idp ./chart -f chart/values.yaml`
5. Verify and test
6. Wire GitHub Actions with SSH key secret; push and confirm

**Verdict placeholder:** _Fill in after PoC_

---

### 5. Railway (comparison only — not k8s)

**Cost:** $5/mo hobby plan; usage-based beyond that.
**K8s:** None. Container-based PaaS.
**GitHub integration:** Best-in-class — push to branch, it deploys. No config required beyond linking the repo.

**Pros**
- Fastest time-to-deployed of any option (minutes from zero)
- GitHub push-to-deploy with zero workflow YAML
- Automatic TLS, domains, env var management
- No infrastructure thinking at all

**Cons**
- No Kubernetes — Helm chart is useless here
- Less control over networking, ingress, sidecar patterns
- Not a realistic representation of how a client would run this in their own cluster
- Harder to replicate client environment for debugging

**PoC steps**
1. railway.app → New Project → Deploy from GitHub repo
2. Select `TheGrandWazoo/mock-idp`; Railway detects Dockerfile automatically
3. Set env vars: `CONFIG_PATH`, `ISS_BASE`, `MOCK_IDP_ADMIN_TOKEN`
4. Verify `/healthz`, issue a token, check playground
5. Make a commit; confirm auto-deploy fires

**Verdict placeholder:** _Fill in after PoC_

---

## Comparison matrix

| | DOKS | Civo | LKE | k3s/Droplet | Railway |
|---|---|---|---|---|---|
| **Monthly cost (entry)** | ~$12 | ~$5 | ~$12 | ~$6 | ~$5 |
| **Kubernetes** | ✓ full | ✓ k3s | ✓ full | ✓ k3s (DIY) | ✗ |
| **Helm chart works** | ✓ | ✓ | ✓ | ✓ | ✗ |
| **Managed control plane** | ✓ | ✓ | ✓ | ✗ | n/a |
| **GitHub Actions deploy** | kubeconfig | kubeconfig | kubeconfig | SSH | automatic |
| **Existing account** | ✓ | ✗ | ✗ | ✓ (DO) | ✗ |
| **Setup time (est.)** | 15 min | 10 min | 20 min | 30 min | 5 min |
| **Client-presentable** | ✓ | ✓ | ✓ | maybe | ✗ (no k8s) |
| **Regions** | 8 | 4 | 11 | 8 (DO) | global |

---

## CI/CD deploy step (GitHub Actions)

Add this job to `.github/workflows/ci.yml` after the build/push job. Works for DOKS,
Civo, and LKE — only the kubeconfig secret name differs.

```yaml
deploy:
  needs: [build]
  runs-on: ubuntu-latest
  if: github.ref == 'refs/heads/main'
  steps:
    - uses: actions/checkout@v4

    - name: Write kubeconfig
      run: |
        mkdir -p ~/.kube
        echo "${{ secrets.KUBECONFIG_B64 }}" | base64 -d > ~/.kube/config
        chmod 600 ~/.kube/config

    - name: Install Helm
      uses: azure/setup-helm@v4

    - name: Deploy
      run: |
        helm upgrade --install mock-idp ./chart \
          --set image.tag=${{ github.sha }} \
          --wait --timeout 120s
```

**Required secrets:**
- `KUBECONFIG_B64` — base64-encoded kubeconfig (`base64 -w0 ~/.kube/config`)

For Railway: no workflow changes needed — push to main triggers deploy automatically.

---

## PoC evaluation criteria

Score each option 1–5 after running the steps above:

| Criterion | Weight | Notes |
|---|---|---|
| Setup time to first token | 20% | From zero to `/healthz` returning 200 |
| GitHub Actions deploy round-trip | 25% | Push commit → running new version |
| Cost per month | 20% | At the smallest viable node size |
| Operational overhead | 20% | What breaks if you ignore it for a month? |
| Client-presentability | 15% | Would you demo this infra to a customer? |

---

## Recommended PoC order

1. **DOKS first** — existing account, lowest friction to get started, establishes the baseline
2. **Civo second** — same Helm workflow, directly comparable, may win on cost
3. **LKE third** — worth the comparison if Civo has gaps
4. **Railway last** — fast to validate the PaaS counterpoint; 30 minutes of effort to confirm or refute the "it's not k8s" concern
5. **k3s/Droplet** — only if cost is the deciding factor and managed k8s loses on price

---

## Related

- [`ADR-001`](ADR-001-python-mock-oidc.md) — original build decision
- [`ADR-002`](ADR-002-provider-plugin-architecture.md) — provider architecture
- [`roadmap.md`](roadmap.md) — feature backlog
- [`manifests/mock-idp.yaml`](../../manifests/mock-idp.yaml) — Kubernetes manifests
- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) — existing CI pipeline
