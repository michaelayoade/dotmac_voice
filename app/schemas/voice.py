import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DOMAIN_RE = re.compile(
    r"^(?=.{1,255}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_DIAL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_*#+-]{1,64}$")


class ExtensionIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    number: str = Field(min_length=1, max_length=32)
    display_name: str = ""

    @field_validator("number")
    @classmethod
    def validate_number(cls, value: str) -> str:
        if not _DIAL_TOKEN_RE.fullmatch(value):
            raise ValueError("number must be a dialable token")
        return value


class DomainIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    fusionpbx_domain: str = Field(min_length=1, max_length=255)
    extensions: list[ExtensionIntent] = []

    @field_validator("fusionpbx_domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        if not _DOMAIN_RE.fullmatch(value):
            raise ValueError("fusionpbx_domain must be a valid DNS name")
        return value

    @model_validator(mode="after")
    def validate_unique_extensions(self) -> "DomainIntent":
        numbers = [ext.number for ext in self.extensions]
        if len(numbers) != len(set(numbers)):
            raise ValueError("extensions must not contain duplicate numbers")
        return self


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
