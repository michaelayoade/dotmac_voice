"""Add TOTP replay counter to MFA methods.

Revision ID: 005_mfa_totp_replay_counter
Revises: 004_notifications
Create Date: 2026-05-04
"""

import sqlalchemy as sa

from alembic import op

revision = "005_mfa_totp_replay_counter"
down_revision = "004_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The regenerated initial schema (799a) already includes this column, so
    # guard the add to keep the migration idempotent on PostgreSQL.
    conn = op.get_bind()
    columns = [c["name"] for c in sa.inspect(conn).get_columns("mfa_methods")]
    if "last_totp_counter" not in columns:
        op.add_column(
            "mfa_methods",
            sa.Column("last_totp_counter", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("mfa_methods", "last_totp_counter")
