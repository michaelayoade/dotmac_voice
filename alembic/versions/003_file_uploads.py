"""003 – file_uploads table

Revision ID: 003_file_uploads
Revises: 002_billing
Create Date: 2026-02-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003_file_uploads"
down_revision = "002_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table("file_uploads"):
        # Create enum type idempotently (SQLAlchemy checkfirst is unreliable
        # inside the single-transaction upgrade on PostgreSQL).
        op.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'fileuploadstatus') "
            "THEN CREATE TYPE fileuploadstatus AS ENUM ('pending', 'active', 'deleted'); END IF; END $$;"
        )

        op.create_table(
            "file_uploads",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("original_filename", sa.String(512), nullable=False),
            sa.Column("content_type", sa.String(120), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("storage_backend", sa.String(20), server_default="local"),
            sa.Column("storage_key", sa.String(512), nullable=False),
            sa.Column("url", sa.Text(), nullable=True),
            sa.Column("category", sa.String(40), server_default="document"),
            sa.Column("entity_type", sa.String(80), nullable=True),
            sa.Column("entity_id", sa.String(120), nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM("pending", "active", "deleted", name="fileuploadstatus", create_type=False),
                server_default="active",
            ),
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
        op.create_index("ix_file_uploads_uploaded_by", "file_uploads", ["uploaded_by"])
        op.create_index("ix_file_uploads_category", "file_uploads", ["category"])
        op.create_index(
            "ix_file_uploads_entity",
            "file_uploads",
            ["entity_type", "entity_id"],
        )


def downgrade() -> None:
    op.drop_table("file_uploads")
    op.execute("DROP TYPE IF EXISTS fileuploadstatus")
