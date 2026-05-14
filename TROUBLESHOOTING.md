# Troubleshooting

Common problems and how to fix them.

---

## Token issues

### `401 invalid_grant`
Wrong username or password for the identity. Verify the credentials match
what is in your `config.yaml`. Check loaded identities at `GET /debug/identities`
(passwords are redacted but presence is confirmed).

### `401 invalid_client`
Wrong `client_id` or `client_secret` for a client credentials grant. Same fix
as above — check `GET /debug/identities`.

### `400 invalid_target`
The requested audience (`resource` or `scope` parameter) is not in the
identity's `allowed_audiences` list and `auth_mode` is `strict`. Either add
the audience to the identity's allowlist in config or switch to `auth_mode: lax`
for the test.

### `400 unsupported_grant_type`
Only `password` and `client_credentials` are supported. Check the
`grant_type` field in your request.

### Token signature invalid after pod restart or key rotation
The signing key is generated fresh on every pod start. Any tokens issued before
the restart are now signed with a key that no longer exists. Re-acquire tokens
after a restart.

If using an API gateway, it may also be holding a stale JWKS cache. Wait for
the cache TTL to expire (typically 60–300s) or force a re-fetch via your
gateway's admin API.

---

## Kong OIDC integration

### Array claims arrive base64-encoded (`X-User-Roles`, `X-User-Groups`, etc.)
This is expected Kong behavior. Kong base64-encodes array-valued JWT claims
before writing them as headers to prevent header injection from characters like
`[`, `"`, and `,`.

Decode in your normalization function before forwarding upstream:
```
WyJvcGVyYXRvciIsInJlc3BvbmRlciJd  →  ["operator","responder"]
```

### Testing missing claims
Use the `X-Omit-Claims` request header when fetching a token to have mock-idp
drop specific claims before signing:
```bash
curl -X POST http://localhost:8080/default/token \
  -H "X-Omit-Claims: roles,groups" \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB"
```
Useful for testing how your normalization function handles absent headers.

### Audience validation failing at the gateway
Confirm the `aud` claim in the token matches what Kong's `config.audience` is
set to. Fetch a token and inspect it at `POST /debug/decode` or the playground
at `GET /`. In `auth_mode: strict`, mock-idp only issues tokens for audiences
in the identity's `allowed_audiences` list.

### JWKS endpoint returns unexpected key after rotation
`POST /admin/rotate-jwks` replaces the in-memory signing key. The new key is
immediately available at `GET /{issuer}/jwks`. If the gateway is not picking
it up, its JWKS cache has not yet expired — wait for the TTL or force a
cache flush.

---

## Local development

### Server starts but tokens fail signature validation
Check that `ISS_BASE` matches the URL you are using to fetch tokens. The issuer
in the token is derived from `ISS_BASE`, and many validators check that the
`iss` claim matches the issuer URL they fetched keys from.

```bash
export ISS_BASE="http://localhost:8080"
uv run uvicorn mock_idp.main:app --reload --port 8080
# Token issuer will be: http://localhost:8080/{issuer-slug}
```

### `CONFIG_PATH` not found
Set the environment variable before starting:
```bash
export CONFIG_PATH="config.example.yaml"
```

### Changes to `config.yaml` not picked up
The identity store is loaded once at startup. Restart the server after editing
the config file. Hot-reload is on the roadmap but not yet available.

### Dependency install fails
Use `uv`, not pip:
```bash
uv sync
```
Do not reference `requirements.txt` — it does not exist in this project.

---

## Kubernetes / Helm

### Pod fails to start — `CrashLoopBackOff`
Check logs:
```bash
kubectl logs -n mock-idp deployment/mock-idp
```
Most common cause: malformed `config.yaml` in the ConfigMap. Fix the YAML,
re-apply, and restart:
```bash
kubectl -n mock-idp edit configmap mock-idp-config
kubectl -n mock-idp rollout restart deployment/mock-idp
```

### `403` on admin endpoints
The `X-Admin-Token` header value does not match. In Kubernetes the token comes
from the `mock-idp-admin-token` Secret via the `MOCK_IDP_ADMIN_TOKEN` env var,
which takes precedence over the `admin_token` field in the ConfigMap.

Verify the Secret exists and has the correct key:
```bash
kubectl -n mock-idp get secret mock-idp-admin-token -o jsonpath='{.data.admin-token}' | base64 -d
```

### `GET /debug/config` shows wrong `iss_base`
The `ISS_BASE` env var is set in the Deployment. For Helm, it defaults to the
in-cluster Service DNS unless `issBase` is set in `values.yaml`:
```bash
helm upgrade mock-idp ./chart --set issBase=https://mock-idp.example.com
```

### Gateway cannot reach the mock in-cluster
Verify NetworkPolicy allows TCP/8080 from gateway pods to the `mock-idp`
namespace. Check the Service is healthy:
```bash
kubectl -n mock-idp get svc mock-idp
kubectl -n mock-idp get endpoints mock-idp
```

### ConfigMap changes not reflected after edit
A pod restart is required:
```bash
kubectl -n mock-idp rollout restart deployment/mock-idp
```

---

## Debug endpoints reference

| Endpoint | What it shows |
|---|---|
| `GET /healthz` | Liveness check — `{"status": "ok"}` |
| `GET /debug/config` | Runtime config: auth_mode, ISS_BASE, identity counts, signing kid |
| `GET /debug/identities` | Loaded users and clients (secrets redacted) |
| `POST /debug/decode` | Decode and verify any JWT — body: `{"token": "<jwt>"}` |
| `GET /{issuer}/.well-known/openid-configuration` | OIDC discovery document |
| `GET /{issuer}/jwks` | Current public signing key |

---

## Still stuck?

Open an issue at [github.com/TheGrandWazoo/mock-idp/issues](https://github.com/TheGrandWazoo/mock-idp/issues).
