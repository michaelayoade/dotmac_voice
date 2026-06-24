"""voice feature desired-state tables (conference, ring group, IVR, queue)

Revision ID: 011_voice_features
Revises: 010_voice_domain_is_active
Create Date: 2026-06-24 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "011_voice_features"
down_revision = "010_voice_domain_is_active"
branch_labels = None
depends_on = None

_TABLES = (
    "voice_conference_rooms",
    "voice_ring_groups",
    "voice_ivr_menus",
    "voice_queues",
)


def _common() -> list:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "voice_domain_id",
            UUID(as_uuid=True),
            sa.ForeignKey("voice_domains.id"),
            nullable=False,
        ),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table("voice_conference_rooms", *_common())
    op.create_table(
        "voice_ring_groups",
        *_common(),
        sa.Column("strategy", sa.String(20), nullable=False, server_default="simultaneous"),
        sa.Column("members", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("timeout", sa.Integer(), nullable=False, server_default="30"),
    )
    op.create_table(
        "voice_ivr_menus",
        *_common(),
        sa.Column(
            "greeting",
            sa.String(255),
            nullable=False,
            server_default="ivr/ivr-enter_ext_pound.wav",
        ),
        sa.Column("options", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "voice_queues",
        *_common(),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("strategy", sa.String(40), nullable=False, server_default="ring-all"),
        sa.Column("agents", sa.JSON(), nullable=False, server_default="[]"),
    )
    for t in _TABLES:
        op.create_index(f"ix_{t}_voice_domain_id", t, ["voice_domain_id"])


def downgrade() -> None:
    for t in reversed(_TABLES):
        op.drop_index(f"ix_{t}_voice_domain_id", table_name=t)
        op.drop_table(t)
