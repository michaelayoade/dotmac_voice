import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExtensionIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    number: str = Field(min_length=1, max_length=32)
    display_name: str = ""


class DomainIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    fusionpbx_domain: str = Field(min_length=1, max_length=255)
    extensions: list[ExtensionIntent] = []


class DomainSyncResult(BaseModel):
    customer_id: str
    sync_status: str


class CdrRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    call_uuid: str
    customer_id: str | None
    direction: str
    caller: str
    callee: str
    start_at: datetime | None
    answer_at: datetime | None
    end_at: datetime | None
    duration_seconds: int
    billsec: int
    hangup_cause: str
    recording_url: str | None
    rating_status: str
    created_at: datetime


class CdrIngestResult(BaseModel):
    call_uuid: str
    rating_status: str
