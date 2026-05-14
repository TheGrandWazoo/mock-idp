# mock-idp

FastAPI mock identity provider emitting configurable OIDC-compliant JWTs for testing API gateway authentication

## Chart version: 0.1.0 — App version: latest

## Installing

```bash
helm upgrade --install mock-idp ./chart \
  -n mock-idp --create-namespace \
  --set ingress.host=mock-idp.example.com \
  --set image.tag=sha-abc1234
```

Override the identity store without touching `values.yaml`:

```bash
helm upgrade --install mock-idp ./chart \
  -n mock-idp \
  -f values.yaml \
  -f environments/staging-values.yaml
```

## Values

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| config | object | `{"admin_token":"change-me-in-real-deployments","auth_mode":"lax","clients":{"00000000-0000-0000-0000-000000000000":{"label":"TestAdmin","override_any_claim":true,"secret":"admin-secret"},"service-a":{"allowed_audiences":["api://serviceB"],"client_id":"01010101-1010-1010-1010-aaaaaaaaaaaa","groups":["api-callers"],"label":"ServiceA","roles":["automation"],"secret":"serviceA-secret","token_version":"v1"}},"cors_allow_origins":["*"],"users":{"alice":{"allowed_audiences":["api://serviceB","api://serviceC"],"extra_claims":{"department":"engineering"},"groups":["support-engineers"],"oid":"11111111-1111-1111-1111-aaaaaaaaaaaa","password":"alice-pw","preferred_username":"alice@example.com","roles":["technician","noc"],"token_lifetime_seconds":300,"token_version":"v2","upn":"alice@example.com"}}}` | Identity store loaded at startup. Serialized as-is into the mounted `config.yaml`. Swap users/clients per environment; do not store real secrets here — use a values override file. |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy. |
| image.repository | string | `"ghcr.io/your-org/mock-idp"` | Image repository. |
| image.tag | string | `""` | Image tag. Defaults to `Chart.appVersion` when empty. |
| ingress.className | string | `"nginx"` | Ingress class name (e.g. `nginx`, `cilium`). |
| ingress.enabled | bool | `true` | Enable the Ingress resource. |
| ingress.host | string | `"mock-idp.example.com"` | Hostname for the Ingress rule and TLS certificate. |
| ingress.tls.enabled | bool | `true` | Enable TLS on the Ingress. |
| ingress.tls.secretName | string | `"kong-wildcard-cert"` | Name of the TLS Secret. |
| issBase | string | `""` | External base URL advertised in tokens and OIDC discovery. Defaults to the in-cluster Service DNS: `http://<fullname>.<namespace>.svc.cluster.local:8080`. |
| replicaCount | int | `1` | Number of replicas. Keep at 1 — signing key is in-memory per pod. |
| resources | object | `{"limits":{"cpu":"200m","memory":"128Mi"},"requests":{"cpu":"50m","memory":"64Mi"}}` | Resource requests and limits for the mock-idp container. |
| service.port | int | `8080` | Service port. |
| service.type | string | `"ClusterIP"` | Service type. |
