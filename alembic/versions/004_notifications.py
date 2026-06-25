"""004 – notifications table

Revision ID: 004_notifications
Revises: 003_file_uploads
Create Date: 2026-02-16
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "004_notifications"
down_revision = "003_file_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table("notifications"):
        op.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'notificationtype') "
            "THEN CREATE TYPE notificationtype AS ENUM ('info', 'success', 'warning', 'error', 'system'); END IF; END $$;"
        )

        op.create_table(
            "notifications",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sender_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column(
                "type",
                postgresql.ENUM(
                    "info",
                    "success",
                    "warning",
                    "error",
                    "system",
                    name="notificationtype",
                    create_type=False,
                ),
                server_default="info",
            ),
            sa.Column("entity_type", sa.String(80), nullable=True),
            sa.Column("entity_id", sa.String(120), nullable=True),
            sa.Column("action_url", sa.String(512), nullable=True),
            sa.Column("is_read", sa.Boolean(), server_default=sa.text("false")),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_notifications_recipient_id", "notifications", ["recipient_id"]
        )
        op.create_index(
            "ix_notifications_recipient_read",
            "notifications",
            ["recipient_id", "is_read"],
        )


def downgrade() -> None:
    op.drop_table("notifications")
    op.execute("DROP TYPE IF EXISTS notificationtype")
