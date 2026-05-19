"""Multi-org schema + token audit log.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-18

Changes
-------
1. Add org_id TEXT NOT NULL DEFAULT 'default' to all existing tables.
2. Rebuild primary keys to include org_id.
3. Rebuild foreign keys from users/service_principals/client_apps to
   reference tenants(org_id, tid) instead of tenants(tid).
4. Replace app_config's boolean single-row constraint with org_id as PK.
5. Create token_events table with indexes.

Design constraint: single-node Postgres today; schema must support
CloudNativePG HA promotion at ~50 paying orgs without a re-migration.
All existing rows land in org_id='default'.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Step 1: add org_id to every table ──────────────────────────────────
    for table in ("app_config", "tenants", "users", "service_principals", "client_apps"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'")

    # ── Step 2: drop FK constraints that reference the old single-column PK ─
    op.execute("ALTER TABLE users DROP CONSTRAINT users_tid_fkey")
    op.execute("ALTER TABLE service_principals DROP CONSTRAINT service_principals_tid_fkey")
    op.execute("ALTER TABLE client_apps DROP CONSTRAINT client_apps_tid_fkey")

    # ── Step 3: rebuild app_config ──────────────────────────────────────────
    # Drop the single-row boolean PK + constraint, promote org_id to PK.
    op.execute("ALTER TABLE app_config DROP CONSTRAINT single_row")
    op.execute("ALTER TABLE app_config DROP CONSTRAINT app_config_pkey")
    op.execute("ALTER TABLE app_config DROP COLUMN id")
    op.execute("ALTER TABLE app_config ADD PRIMARY KEY (org_id)")

    # ── Step 4: rebuild tenants PK ──────────────────────────────────────────
    op.execute("ALTER TABLE tenants DROP CONSTRAINT tenants_pkey")
    op.execute("ALTER TABLE tenants ADD PRIMARY KEY (org_id, tid)")

    # ── Step 5: rebuild users PK + FK ───────────────────────────────────────
    op.execute("ALTER TABLE users DROP CONSTRAINT users_pkey")
    op.execute("ALTER TABLE users ADD PRIMARY KEY (org_id, username, tid)")
    op.execute("""
        ALTER TABLE users
            ADD CONSTRAINT users_org_tid_fkey
            FOREIGN KEY (org_id, tid) REFERENCES tenants(org_id, tid) ON DELETE CASCADE
    """)

    # ── Step 6: rebuild service_principals PK + FK ──────────────────────────
    op.execute("ALTER TABLE service_principals DROP CONSTRAINT service_principals_pkey")
    op.execute("ALTER TABLE service_principals ADD PRIMARY KEY (org_id, name, tid)")
    op.execute("""
        ALTER TABLE service_principals
            ADD CONSTRAINT sps_org_tid_fkey
            FOREIGN KEY (org_id, tid) REFERENCES tenants(org_id, tid) ON DELETE CASCADE
    """)

    # ── Step 7: rebuild client_apps PK + FK ─────────────────────────────────
    op.execute("ALTER TABLE client_apps DROP CONSTRAINT client_apps_pkey")
    op.execute("ALTER TABLE client_apps ADD PRIMARY KEY (org_id, audience, tid)")
    op.execute("""
        ALTER TABLE client_apps
            ADD CONSTRAINT apps_org_tid_fkey
            FOREIGN KEY (org_id, tid) REFERENCES tenants(org_id, tid) ON DELETE CASCADE
    """)

    # ── Step 8: create token_events ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE token_events (
            id          BIGSERIAL    PRIMARY KEY,
            org_id      TEXT         NOT NULL DEFAULT 'default',
            issuer      TEXT         NOT NULL,
            grant_type  TEXT         NOT NULL,
            sub         TEXT         NOT NULL,
            aud         TEXT,
            iat         BIGINT       NOT NULL,
            exp         BIGINT       NOT NULL,
            jti         TEXT,
            raw_claims  JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_token_events_org_created ON token_events (org_id, created_at DESC)"
    )


def downgrade() -> None:
    # ── Reverse token_events ─────────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_token_events_org_created")
    op.execute("DROP TABLE IF EXISTS token_events")

    # ── Restore client_apps ──────────────────────────────────────────────────
    op.execute("ALTER TABLE client_apps DROP CONSTRAINT IF EXISTS apps_org_tid_fkey")
    op.execute("ALTER TABLE client_apps DROP CONSTRAINT client_apps_pkey")
    op.execute("ALTER TABLE client_apps ADD PRIMARY KEY (audience, tid)")
    op.execute("""
        ALTER TABLE client_apps
            ADD CONSTRAINT client_apps_tid_fkey
            FOREIGN KEY (tid) REFERENCES tenants(tid) ON DELETE CASCADE
    """)
    op.execute("ALTER TABLE client_apps DROP COLUMN org_id")

    # ── Restore service_principals ───────────────────────────────────────────
    op.execute("ALTER TABLE service_principals DROP CONSTRAINT IF EXISTS sps_org_tid_fkey")
    op.execute("ALTER TABLE service_principals DROP CONSTRAINT service_principals_pkey")
    op.execute("ALTER TABLE service_principals ADD PRIMARY KEY (name, tid)")
    op.execute("""
        ALTER TABLE service_principals
            ADD CONSTRAINT service_principals_tid_fkey
            FOREIGN KEY (tid) REFERENCES tenants(tid) ON DELETE CASCADE
    """)
    op.execute("ALTER TABLE service_principals DROP COLUMN org_id")

    # ── Restore users ────────────────────────────────────────────────────────
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_org_tid_fkey")
    op.execute("ALTER TABLE users DROP CONSTRAINT users_pkey")
    op.execute("ALTER TABLE users ADD PRIMARY KEY (username, tid)")
    op.execute("""
        ALTER TABLE users
            ADD CONSTRAINT users_tid_fkey
            FOREIGN KEY (tid) REFERENCES tenants(tid) ON DELETE CASCADE
    """)
    op.execute("ALTER TABLE users DROP COLUMN org_id")

    # ── Restore tenants ──────────────────────────────────────────────────────
    op.execute("ALTER TABLE tenants DROP CONSTRAINT tenants_pkey")
    op.execute("ALTER TABLE tenants ADD PRIMARY KEY (tid)")
    op.execute("ALTER TABLE tenants DROP COLUMN org_id")

    # ── Restore app_config ───────────────────────────────────────────────────
    op.execute("ALTER TABLE app_config DROP CONSTRAINT app_config_pkey")
    op.execute("ALTER TABLE app_config ADD COLUMN id BOOLEAN PRIMARY KEY DEFAULT TRUE")
    op.execute("ALTER TABLE app_config ADD CONSTRAINT single_row CHECK (id)")
    op.execute("ALTER TABLE app_config DROP COLUMN org_id")
