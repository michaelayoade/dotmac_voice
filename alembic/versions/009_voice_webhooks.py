"""add voice_webhook_endpoints and voice_webhook_deliveries tables

Revision ID: 009_voice_webhooks
Revises: 008_voice_cdrs
Create Date: 2026-06-22 00:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "009_voice_webhooks"
down_revision = "008_voice_cdrs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'deliverystatus') "
        "THEN CREATE TYPE deliverystatus AS ENUM ('pending', 'delivered', 'failed'); END IF; END $$;"
    )

    if not inspector.has_table("voice_webhook_endpoints"):
        op.create_table(
            "voice_webhook_endpoints",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("url", sa.String(512), nullable=False),
            sa.Column("secret", sa.String(128), nullable=False),
            sa.Column("event_types", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if not inspector.has_table("voice_webhook_deliveries"):
        op.create_table(
            "voice_webhook_deliveries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "endpoint_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("voice_webhook_endpoints.id"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "pending",
                    "delivered",
                    "failed",
                    name="deliverystatus",
                    create_type=False,
                ),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_status_code", sa.Integer(), nullable=True),
            sa.Column("last_error", sa.String(512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_voice_webhook_deliveries_endpoint_id",
            "voice_webhook_deliveries",
            ["endpoint_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_voice_webhook_deliveries_endpoint_id",
        table_name="voice_webhook_deliveries",
    )
    op.drop_table("voice_webhook_deliveries")
    op.drop_table("voice_webhook_endpoints")
    op.execute("DROP TYPE IF EXISTS deliverystatus")
