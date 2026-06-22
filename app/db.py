from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """Mixin that adds created_at / updated_at columns to any model.

    Usage::

        class MyModel(TimestampMixin, Base):
            __tablename__ = "my_table"
            ...
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


def get_engine():
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
    )
    _configure_statement_timeout(engine)
    return engine


def _configure_statement_timeout(engine: Engine) -> None:
    if not settings.database_url.startswith("postgresql"):
        return
    timeout_ms = max(settings.db_statement_timeout_ms, 0)
    if timeout_ms == 0:
        return

    @event.listens_for(engine, "connect")
    def _set_statement_timeout(dbapi_connection, _connection_record) -> None:
        with dbapi_connection.cursor() as cursor:
            # PostgreSQL SET does not accept bind parameters; timeout_ms is a
            # validated int so literal interpolation is safe.
            cursor.execute(f"SET statement_timeout = {timeout_ms}")


SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
