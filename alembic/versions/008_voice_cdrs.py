"""add voice_cdrs table

Revision ID: 008_voice_cdrs
Revises: 007_voice_domains
Create Date: 2026-06-22 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008_voice_cdrs"
down_revision = "007_voice_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'cdrratingstatus') "
        "THEN CREATE TYPE cdrratingstatus AS ENUM ('raw', 'rated', 'fed'); END IF; END $$;"
    )

    if not inspector.has_table("voice_cdrs"):
        op.create_table(
            "voice_cdrs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("call_uuid", sa.String(64), nullable=False),
            sa.Column("customer_id", sa.String(64), nullable=True),
            sa.Column("direction", sa.String(16), nullable=False, server_default=""),
            sa.Column("caller", sa.String(64), nullable=False, server_default=""),
            sa.Column("callee", sa.String(64), nullable=False, server_default=""),
            sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("answer_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("billsec", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hangup_cause", sa.String(64), nullable=False, server_default=""),
            sa.Column("recording_url", sa.String(512), nullable=True),
            sa.Column(
                "rating_status",
                postgresql.ENUM("raw", "rated", "fed", name="cdrratingstatus", create_type=False),
                nullable=False,
                server_default="raw",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_voice_cdrs_call_uuid", "voice_cdrs", ["call_uuid"])
        op.create_index("ix_voice_cdrs_customer_id", "voice_cdrs", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_cdrs_customer_id", table_name="voice_cdrs")
    op.drop_index("ix_voice_cdrs_call_uuid", table_name="voice_cdrs")
    op.drop_table("voice_cdrs")
    op.execute("DROP TYPE IF EXISTS cdrratingstatus")
