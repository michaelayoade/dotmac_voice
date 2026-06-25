"""voice domain is_active (suspend/resume)

Revision ID: 010_voice_domain_is_active
Revises: 009_voice_webhooks
Create Date: 2026-06-24 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "010_voice_domain_is_active"
down_revision = "009_voice_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_domains",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("voice_domains", "is_active")
