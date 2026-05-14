from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator


class UserRecord(BaseModel):
    password: str
    upn: Optional[str] = None
    preferred_username: Optional[str] = None
    oid: str = "00000000-0000-0000-0000-000000000000"
    tid: str = "22222222-2222-2222-2222-222222222222"
    token_version: str = "v2"
    token_lifetime_seconds: int = 3600
    roles: list[str] = []
    groups: list[str] = []
    allowed_audiences: list[str] = []
    extra_claims: dict[str, Any] = {}

    @field_validator("token_version")
    @classmethod
    def _valid_version(cls, v: str) -> str:
        if v not in ("v1", "v2"):
            raise ValueError("token_version must be 'v1' or 'v2'")
        return v


class ClientRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: Optional[str] = None
    secret: str
    label: Optional[str] = None
    token_version: str = "v1"
    token_lifetime_seconds: int = 3600
    roles: list[str] = []
    groups: list[str] = []
    tid: str = "22222222-2222-2222-2222-222222222222"
    allowed_audiences: list[str] = []
    extra_claims: dict[str, Any] = {}
    override_any_claim: bool = False
    _canonical_id: str = PrivateAttr(default="")

    @field_validator("token_version")
    @classmethod
    def _valid_version(cls, v: str) -> str:
        if v not in ("v1", "v2"):
            raise ValueError("token_version must be 'v1' or 'v2'")
        return v


class AppConfig(BaseModel):
    auth_mode: str = "lax"
    cors_allow_origins: list[str] = ["*"]
    admin_token: str = "change-me"
    users: dict[str, UserRecord] = {}
    clients: dict[str, ClientRecord] = {}

    @field_validator("auth_mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        v = v.lower()
        if v not in ("lax", "strict"):
            raise ValueError("auth_mode must be 'lax' or 'strict'")
        return v


class DecodeRequest(BaseModel):
    token: str
