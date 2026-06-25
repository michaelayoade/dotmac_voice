"""voice domain recording_enabled

Revision ID: 012_voice_domain_recording
Revises: 011_voice_features
Create Date: 2026-06-25 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "012_voice_domain_recording"
down_revision = "011_voice_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_domains",
        sa.Column(
            "recording_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("voice_domains", "recording_enabled")
