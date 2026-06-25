"""Tests for TimestampMixin and database base classes."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, TimestampMixin


# Create a test model using the mixin
class _TestModel(TimestampMixin, Base):
    __tablename__ = "test_timestamp_model"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))


_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@pytest.fixture
def db() -> Session:
    session = _SessionLocal()
    yield session
    session.rollback()
    session.close()


class TestTimestampMixin:
    def test_created_at_set_on_insert(self, db: Session) -> None:
        obj = _TestModel(name="test")
        db.add(obj)
        db.flush()
        assert obj.created_at is not None
        assert isinstance(obj.created_at, datetime)

    def test_updated_at_set_on_insert(self, db: Session) -> None:
        obj = _TestModel(name="test")
        db.add(obj)
        db.flush()
        assert obj.updated_at is not None

    def test_mixin_columns_exist(self) -> None:
        """TimestampMixin adds created_at and updated_at columns."""
        columns = {c.name for c in _TestModel.__table__.columns}
        assert "created_at" in columns
        assert "updated_at" in columns
