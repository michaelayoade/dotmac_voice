"""add voice_domains and voice_extensions

Revision ID: 007_voice_domains
Revises: 006_branding_setting_domain
Create Date: 2026-06-21 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "007_voice_domains"
down_revision = "006_branding_setting_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    sync_status = postgresql.ENUM(
        "pending", "synced", "drift", "error", name="syncstatus", create_type=False
    )
    sync_status.create(conn, checkfirst=True)
    if not inspector.has_table("voice_domains"):
        op.create_table(
            "voice_domains",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("customer_id", sa.String(64), nullable=False),
            sa.Column("fusionpbx_domain", sa.String(255), nullable=False),
            sa.Column(
                "sync_status",
                sa.Enum(
                    "pending",
                    "synced",
                    "drift",
                    "error",
                    name="syncstatus",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_voice_domains_customer_id", "voice_domains", ["customer_id"], unique=True
        )
    if not inspector.has_table("voice_extensions"):
        op.create_table(
            "voice_extensions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "voice_domain_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("voice_domains.id"),
                nullable=False,
            ),
            sa.Column("number", sa.String(32), nullable=False),
            sa.Column("display_name", sa.String(120), nullable=False),
            sa.Column("voicemail_enabled", sa.Boolean(), nullable=False),
            sa.Column(
                "sync_status",
                sa.Enum(
                    "pending",
                    "synced",
                    "drift",
                    "error",
                    name="syncstatus",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_voice_extensions_voice_domain_id",
            "voice_extensions",
            ["voice_domain_id"],
        )


def downgrade() -> None:
    op.drop_table("voice_extensions")
    op.drop_table("voice_domains")
    op.execute("DROP TYPE IF EXISTS syncstatus")
