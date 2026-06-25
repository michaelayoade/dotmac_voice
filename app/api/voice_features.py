"""Per-feature voice provisioning endpoints (conference, ring group, IVR, queue).

Model-based desired state: each write upserts/deletes the feature model, then
``reconcile_voice`` applies it to FusionPBX (create/update) and drift-deletes
anything undefined. ``require_ingress`` auth (consumed by sub / crm / self-care).
"""

import re
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.provisioning import _commit, get_fusionpbx_client
from app.db import get_db
from app.models.voice import (
    ConferenceRoom,
    IvrMenu,
    Queue,
    RingGroup,
    VoiceDomain,
)
from app.schemas.voice import DomainSyncResult
from app.services.exceptions import NotFoundError
from app.services.fusionpbx.client import FusionpbxClient
from app.services.ingress_auth import require_ingress
from app.services.reconcile.voice import reconcile_voice

_DIAL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_*#+-]{1,64}$")


def _validate_dial_token(value: str, field: str) -> str:
    if not _DIAL_TOKEN_RE.fullmatch(value):
        raise ValueError(f"{field} must be a dialable token")
    return value


router = APIRouter(
    prefix="/provisioning/domains/{customer_id}/features",
    tags=["voice-features"],
    dependencies=[Depends(require_ingress)],
)


def _domain(db: Session, customer_id: str) -> VoiceDomain:
    dom = db.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == customer_id))
    if not dom:
        raise NotFoundError(f"No voice domain for customer {customer_id}")
    return dom


def _sync(db: Session, client: FusionpbxClient, customer_id: str) -> DomainSyncResult:
    status = reconcile_voice(db, client, customer_id)
    _commit(db)
    return DomainSyncResult(customer_id=customer_id, sync_status=status.value)


def _upsert(db: Session, dom: VoiceDomain, model_cls, number: str, **fields) -> None:
    obj = db.scalar(
        select(model_cls).where(
            model_cls.voice_domain_id == dom.id, model_cls.number == number
        )
    )
    if obj is not None:
        for k, v in fields.items():
            setattr(obj, k, v)
    else:
        db.add(model_cls(voice_domain_id=dom.id, number=number, **fields))
    db.flush()


def _delete(db: Session, dom: VoiceDomain, model_cls, number: str) -> None:
    obj = db.scalar(
        select(model_cls).where(
            model_cls.voice_domain_id == dom.id, model_cls.number == number
        )
    )
    if obj is not None:
        db.delete(obj)
        db.flush()


class ConferenceIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)

    @field_validator("number")
    @classmethod
    def validate_number(cls, value: str) -> str:
        return _validate_dial_token(value, "number")


class RingGroupIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    members: list[str] = Field(min_length=1)
    strategy: Literal["simultaneous", "sequence"] = "simultaneous"
    timeout: int = Field(default=30, ge=1, le=120)

    @field_validator("number")
    @classmethod
    def validate_number(cls, value: str) -> str:
        return _validate_dial_token(value, "number")

    @field_validator("members")
    @classmethod
    def validate_members(cls, value: list[str]) -> list[str]:
        for member in value:
            _validate_dial_token(member, "member")
        if len(value) != len(set(value)):
            raise ValueError("members must not contain duplicates")
        return value


class IvrIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    options: dict[str, str] = Field(min_length=1)
    greeting: str = "ivr/ivr-enter_ext_pound.wav"

    @field_validator("number")
    @classmethod
    def validate_number(cls, value: str) -> str:
        return _validate_dial_token(value, "number")

    @model_validator(mode="after")
    def validate_options(self) -> "IvrIntent":
        for digit, target in self.options.items():
            if not re.fullmatch(r"[0-9]", digit):
                raise ValueError("IVR option keys must be single digits")
            _validate_dial_token(target, "IVR target")
        return self


class QueueIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    agents: list[str] = Field(min_length=1)
    name: str = ""
    strategy: Literal[
        "ring-all",
        "longest-idle-agent",
        "round-robin",
        "top-down",
        "agent-with-least-talk-time",
        "agent-with-fewest-calls",
        "sequentially-by-agent-order",
        "random",
    ] = "ring-all"

    @field_validator("number")
    @classmethod
    def validate_number(cls, value: str) -> str:
        return _validate_dial_token(value, "number")

    @field_validator("agents")
    @classmethod
    def validate_agents(cls, value: list[str]) -> list[str]:
        for agent in value:
            _validate_dial_token(agent, "agent")
        if len(value) != len(set(value)):
            raise ValueError("agents must not contain duplicates")
        return value


@router.post("/conferences", response_model=DomainSyncResult)
def post_conference(customer_id, payload: ConferenceIntent, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _upsert(db, _domain(db, customer_id), ConferenceRoom, payload.number)
    return _sync(db, client, customer_id)


@router.delete("/conferences/{number}", response_model=DomainSyncResult)
def delete_conference(customer_id, number: str, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _delete(db, _domain(db, customer_id), ConferenceRoom, number)
    return _sync(db, client, customer_id)


@router.post("/ring-groups", response_model=DomainSyncResult)
def post_ring_group(customer_id, payload: RingGroupIntent, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _upsert(
        db, _domain(db, customer_id), RingGroup, payload.number,
        members=payload.members, strategy=payload.strategy, timeout=payload.timeout,
    )
    return _sync(db, client, customer_id)


@router.delete("/ring-groups/{number}", response_model=DomainSyncResult)
def delete_ring_group(customer_id, number: str, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _delete(db, _domain(db, customer_id), RingGroup, number)
    return _sync(db, client, customer_id)


@router.post("/ivrs", response_model=DomainSyncResult)
def post_ivr(customer_id, payload: IvrIntent, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _upsert(
        db, _domain(db, customer_id), IvrMenu, payload.number,
        options=payload.options, greeting=payload.greeting,
    )
    return _sync(db, client, customer_id)


@router.delete("/ivrs/{number}", response_model=DomainSyncResult)
def delete_ivr(customer_id, number: str, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _delete(db, _domain(db, customer_id), IvrMenu, number)
    return _sync(db, client, customer_id)


@router.post("/queues", response_model=DomainSyncResult)
def post_queue(customer_id, payload: QueueIntent, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _upsert(
        db, _domain(db, customer_id), Queue, payload.number,
        agents=payload.agents, name=payload.name, strategy=payload.strategy,
    )
    return _sync(db, client, customer_id)


@router.delete("/queues/{number}", response_model=DomainSyncResult)
def delete_queue(customer_id, number: str, db=Depends(get_db), client=Depends(get_fusionpbx_client)):
    _delete(db, _domain(db, customer_id), Queue, number)
    return _sync(db, client, customer_id)
