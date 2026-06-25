import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String
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
    # Service state: suspended (False) -> reconcile removes the customer's FusionPBX
    # extensions (can't register/call) while preserving the dotmac_voice models.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When True, reconcile provisions call recording on the internal routing.
    recording_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
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


def _now() -> datetime:
    return datetime.now(UTC)


class _FeatureBase(Base):
    """Shared columns for per-domain feature desired-state."""

    __abstract__ = True
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    voice_domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_domains.id"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class ConferenceRoom(_FeatureBase):
    __tablename__ = "voice_conference_rooms"


class RingGroup(_FeatureBase):
    __tablename__ = "voice_ring_groups"
    strategy: Mapped[str] = mapped_column(String(20), nullable=False, default="simultaneous")
    members: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=30)


class IvrMenu(_FeatureBase):
    __tablename__ = "voice_ivr_menus"
    greeting: Mapped[str] = mapped_column(
        String(255), nullable=False, default="ivr/ivr-enter_ext_pound.wav"
    )
    options: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Queue(_FeatureBase):
    __tablename__ = "voice_queues"
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    strategy: Mapped[str] = mapped_column(String(40), nullable=False, default="ring-all")
    agents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
