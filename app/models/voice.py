import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SyncStatus(enum.Enum):
    pending = "pending"
    synced = "synced"
    drift = "drift"
    error = "error"


class VoiceDomain(Base):
    __tablename__ = "voice_domains"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, unique=True
    )
    fusionpbx_domain: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus), default=SyncStatus.pending, nullable=False
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Extension(Base):
    __tablename__ = "voice_extensions"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    voice_domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_domains.id"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    voicemail_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus), default=SyncStatus.pending, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class CdrRatingStatus(enum.Enum):
    raw = "raw"
    rated = "rated"
    fed = "fed"


class Cdr(Base):
    __tablename__ = "voice_cdrs"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    call_uuid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    caller: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    callee: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billsec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hangup_cause: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    recording_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rating_status: Mapped[CdrRatingStatus] = mapped_column(
        Enum(CdrRatingStatus), default=CdrRatingStatus.raw, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
