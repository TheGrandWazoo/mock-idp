"""Initial schema for mock-idp Postgres backend.

Revision ID: 0001
Revises:
Create Date: 2026-05-15

Tables
------
app_config          Single-row global config (auth_mode, admin_token, cors, issuer_modes)
tenants             One row per tenant (tid, provider)
users               Password-grant identities; PK is (username, tid)
service_principals  Client-credentials identities; PK is (name, tid)
client_apps         Resource apps with role definitions and per-identity grants

Array columns use Postgres TEXT[] for list fields.
JSONB is used for maps (extra_claims, grants, issuer_modes).
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS app_config (
            id          BOOLEAN PRIMARY KEY DEFAULT TRUE,
            auth_mode   TEXT    NOT NULL DEFAULT 'lax',
            cors_allow_origins TEXT[] NOT NULL DEFAULT ARRAY['*'],
            admin_token TEXT    NOT NULL DEFAULT 'change-me',
            issuer_modes JSONB  NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT single_row CHECK (id)
        )
    """)
    # Seed the single config row so SELECT always returns a row.
    op.execute("INSERT INTO app_config DEFAULT VALUES ON CONFLICT DO NOTHING")

    op.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            tid      TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'entra_id'
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username              TEXT NOT NULL,
            tid                   TEXT NOT NULL REFERENCES tenants(tid) ON DELETE CASCADE,
            password              TEXT NOT NULL,
            upn                   TEXT,
            preferred_username    TEXT,
            oid                   TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
            token_version         TEXT NOT NULL DEFAULT 'v2',
            token_lifetime_seconds INT  NOT NULL DEFAULT 3600,
            roles                 TEXT[]  NOT NULL DEFAULT ARRAY[]::TEXT[],
            groups                TEXT[]  NOT NULL DEFAULT ARRAY[]::TEXT[],
            allowed_audiences     TEXT[]  NOT NULL DEFAULT ARRAY[]::TEXT[],
            extra_claims          JSONB   NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (username, tid)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS service_principals (
            name                  TEXT NOT NULL,
            tid                   TEXT NOT NULL REFERENCES tenants(tid) ON DELETE CASCADE,
            client_id             TEXT,
            secret                TEXT NOT NULL,
            label                 TEXT,
            token_version         TEXT    NOT NULL DEFAULT 'v1',
            token_lifetime_seconds INT    NOT NULL DEFAULT 3600,
            roles                 TEXT[]  NOT NULL DEFAULT ARRAY[]::TEXT[],
            groups                TEXT[]  NOT NULL DEFAULT ARRAY[]::TEXT[],
            allowed_audiences     TEXT[]  NOT NULL DEFAULT ARRAY[]::TEXT[],
            extra_claims          JSONB   NOT NULL DEFAULT '{}'::jsonb,
            override_any_claim    BOOLEAN NOT NULL DEFAULT FALSE,
            override_iss_too      BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (name, tid)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS client_apps (
            audience TEXT NOT NULL,
            tid      TEXT NOT NULL REFERENCES tenants(tid) ON DELETE CASCADE,
            app_id   TEXT,
            label    TEXT,
            roles    TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
            grants   JSONB  NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (audience, tid)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS client_apps")
    op.execute("DROP TABLE IF EXISTS service_principals")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS tenants")
    op.execute("DROP TABLE IF EXISTS app_config")
