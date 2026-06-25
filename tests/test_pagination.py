"""Tests for the paginate() helper and common utilities."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.common import apply_ordering, apply_pagination, coerce_uuid, paginate

# ── Test DB setup ────────────────────────────────────────


class _Base(DeclarativeBase):
    pass


class _Item(_Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))


_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@pytest.fixture
def db() -> Session:
    session = _SessionLocal()
    # Seed data
    for i in range(50):
        session.add(_Item(name=f"Item {i:03d}"))
    session.commit()
    yield session
    session.close()
    # Clean up
    with _SessionLocal() as s:
        s.query(_Item).delete()
        s.commit()


class TestCoerceUuid:
    def test_none_returns_none(self) -> None:
        assert coerce_uuid(None) is None

    def test_uuid_passthrough(self) -> None:
        u = uuid.uuid4()
        assert coerce_uuid(u) is u

    def test_string_to_uuid(self) -> None:
        s = "12345678-1234-5678-1234-567812345678"
        result = coerce_uuid(s)
        assert isinstance(result, uuid.UUID)
        assert str(result) == s

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            coerce_uuid("not-a-uuid")


class TestPaginate:
    def test_first_page(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        result = paginate(db, query, page=1, page_size=10)
        assert result["total"] == 50
        assert result["page"] == 1
        assert result["page_size"] == 10
        assert result["pages"] == 5
        assert len(result["items"]) == 10

    def test_last_page(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        result = paginate(db, query, page=5, page_size=10)
        assert len(result["items"]) == 10
        assert result["page"] == 5

    def test_beyond_last_page(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        result = paginate(db, query, page=999, page_size=10)
        assert len(result["items"]) == 0
        assert result["total"] == 50

    def test_page_size_capped_at_max(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        result = paginate(db, query, page=1, page_size=999, max_page_size=25)
        assert result["page_size"] == 25
        assert len(result["items"]) == 25

    def test_negative_page_becomes_1(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        result = paginate(db, query, page=-5, page_size=10)
        assert result["page"] == 1

    def test_zero_page_becomes_1(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        result = paginate(db, query, page=0, page_size=10)
        assert result["page"] == 1

    def test_uneven_last_page(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        result = paginate(db, query, page=1, page_size=15)
        assert result["pages"] == 4  # ceil(50/15) = 4
        # Last page should have 5 items
        last = paginate(db, query, page=4, page_size=15)
        assert len(last["items"]) == 5


class TestApplyOrdering:
    def test_valid_asc(self, db: Session) -> None:
        query = select(_Item)
        allowed = {"name": _Item.name, "id": _Item.id}
        ordered = apply_ordering(query, "name", "asc", allowed)
        items = list(db.scalars(ordered).all())
        assert items[0].name == "Item 000"

    def test_valid_desc(self, db: Session) -> None:
        query = select(_Item)
        allowed = {"name": _Item.name, "id": _Item.id}
        ordered = apply_ordering(query, "name", "desc", allowed)
        items = list(db.scalars(ordered).all())
        assert items[0].name == "Item 049"

    def test_invalid_column_raises(self, db: Session) -> None:
        from fastapi import HTTPException

        query = select(_Item)
        with pytest.raises(HTTPException) as exc_info:
            apply_ordering(query, "invalid", "asc", {"name": _Item.name})
        assert exc_info.value.status_code == 400


class TestApplyPagination:
    def test_limit_offset(self, db: Session) -> None:
        query = select(_Item).order_by(_Item.id)
        paginated = apply_pagination(query, limit=5, offset=10)
        items = list(db.scalars(paginated).all())
        assert len(items) == 5
