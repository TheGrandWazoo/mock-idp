"""Add orgs + org_jwks tables for hosted Pro mode.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-19

Changes
-------
1. Create ``orgs`` table — slug registry, tier, API key hash.
2. Create ``org_jwks`` table — per-org, per-issuer key material persisted
   so JWKS URLs remain stable across pod restarts.
3. Seed the pre-provisioned ``sandbox`` org (no API key, read-only config).
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE orgs (
            org_id        TEXT        PRIMARY KEY,
            slug          TEXT        NOT NULL UNIQUE,
            tier          TEXT        NOT NULL DEFAULT 'pro',
            api_key_hash  TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE org_jwks (
            org_id       TEXT        NOT NULL REFERENCES orgs(org_id) ON DELETE CASCADE,
            issuer_slug  TEXT        NOT NULL,
            key_data     JSONB       NOT NULL,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, issuer_slug)
        )
    """)

    # Seed sandbox — no API key, aggressive rate limit enforced at the app layer
    op.execute("""
        INSERT INTO orgs (org_id, slug, tier, api_key_hash)
        VALUES ('sandbox', 'sandbox', 'sandbox', NULL)
        ON CONFLICT DO NOTHING
    """)

    # Minimal app_config for sandbox org so the store can start
    op.execute("""
        INSERT INTO app_config (org_id, auth_mode, admin_token, cors_allow_origins, issuer_modes)
        VALUES ('sandbox', 'lax', 'sandbox-admin', '{\"*\"}', '{}')
        ON CONFLICT (org_id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_jwks")
    op.execute("DELETE FROM app_config WHERE org_id = 'sandbox'")
    op.execute("DROP TABLE IF EXISTS orgs")
