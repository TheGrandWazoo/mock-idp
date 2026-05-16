from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator


class UserRecord(BaseModel):
    password: str
    upn: Optional[str] = None
    preferred_username: Optional[str] = None
    oid: str = "00000000-0000-0000-0000-000000000000"
    tid: str = ""  # injected from tenant key at load time
    token_version: str = "v2"
    token_lifetime_seconds: int = 3600
    signing_alg: str = "RS256"
    realm_roles: list[str] = []  # always in token, any audience (identity-level)
    _tenant_realm_roles: list[str] = PrivateAttr(default_factory=list)  # injected from tenant
    roles: list[str] = []  # fallback when no client-app grants are configured
    groups: list[str] = []
    allowed_audiences: list[str] = []
    extra_claims: dict[str, Any] = {}

    @field_validator("password", mode="before")
    @classmethod
    def _coerce_password(cls, v: object) -> str:
        return str(v)

    @field_validator("token_version")
    @classmethod
    def _valid_version(cls, v: str) -> str:
        if v not in ("v1", "v2"):
            raise ValueError("token_version must be 'v1' or 'v2'")
        return v

    @field_validator("signing_alg")
    @classmethod
    def _valid_signing_alg(cls, v: str) -> str:
        if v not in ("RS256", "ES256"):
            raise ValueError("signing_alg must be 'RS256' or 'ES256'")
        return v


class ServicePrincipalRecord(BaseModel):
    """Machine identity that requests tokens (client_credentials grant)."""

    model_config = ConfigDict(populate_by_name=True)

    client_id: Optional[str] = None
    secret: str
    label: Optional[str] = None
    token_version: str = "v1"
    token_lifetime_seconds: int = 3600
    signing_alg: str = "RS256"
    realm_roles: list[str] = []  # always in token, any audience (identity-level)
    _tenant_realm_roles: list[str] = PrivateAttr(default_factory=list)  # injected from tenant
    roles: list[str] = []  # fallback when no client-app grants are configured
    groups: list[str] = []
    tid: str = ""  # injected from tenant key at load time
    allowed_audiences: list[str] = []
    extra_claims: dict[str, Any] = {}
    override_any_claim: bool = False
    override_iss_too: bool = False  # explicit second flag required to override iss
    _canonical_id: str = PrivateAttr(default="")
    _name: str = PrivateAttr(default="")  # original key in service_principals; used for grants lookup

    @field_validator("secret", mode="before")
    @classmethod
    def _coerce_secret(cls, v: object) -> str:
        return str(v)

    @field_validator("token_version")
    @classmethod
    def _valid_version(cls, v: str) -> str:
        if v not in ("v1", "v2"):
            raise ValueError("token_version must be 'v1' or 'v2'")
        return v

    @field_validator("signing_alg")
    @classmethod
    def _valid_signing_alg(cls, v: str) -> str:
        if v not in ("RS256", "ES256"):
            raise ValueError("signing_alg must be 'RS256' or 'ES256'")
        return v


class ClientAppRecord(BaseModel):
    """Resource application — defines available roles and per-identity grants."""

    app_id: Optional[str] = None
    label: Optional[str] = None
    roles: list[str] = []
    grants: dict[str, list[str]] = {}


class TenantRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "entra_id"
    realm_roles: list[str] = []  # applied to every identity in this tenant
    users: dict[str, UserRecord] = {}
    service_principals: dict[str, ServicePrincipalRecord] = {}
    clients: dict[str, ClientAppRecord] = {}  # resource apps keyed by audience URI


class WebhookConfig(BaseModel):
    """One webhook destination."""

    url: str
    events: list[str] = ["token_issued"]
    timeout_seconds: float = 5.0


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_mode: str = "lax"
    cors_allow_origins: list[str] = ["*"]
    admin_token: str = "change-me"
    issuer_modes: dict[str, str] = {}  # per-issuer-slug auth_mode overrides
    webhooks: list[WebhookConfig] = []
    tenants: dict[str, TenantRecord] = {}

    @field_validator("auth_mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        v = v.lower()
        if v not in ("lax", "strict"):
            raise ValueError("auth_mode must be 'lax' or 'strict'")
        return v

    @field_validator("issuer_modes")
    @classmethod
    def _valid_issuer_modes(cls, v: dict) -> dict:
        result = {}
        for slug, mode in v.items():
            mode = str(mode).lower()
            if mode not in ("lax", "strict"):
                raise ValueError(f"issuer_modes[{slug!r}] must be 'lax' or 'strict'")
            result[slug] = mode
        return result


class DecodeRequest(BaseModel):
    token: str
