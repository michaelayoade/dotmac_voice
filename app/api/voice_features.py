"""Per-feature voice provisioning endpoints (conference, ring group, IVR, queue).

Thin idempotent wrappers over the FusionpbxClient primitives: resolve the
customer's FusionPBX domain, then call the primitive. ``require_ingress`` auth
(consumed by sub / crm / self-care). These replace the raw SQL/ESL that this
session used by hand.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.provisioning import get_fusionpbx_client
from app.db import get_db
from app.models.voice import VoiceDomain
from app.services.exceptions import NotFoundError
from app.services.fusionpbx.client import FusionpbxClient
from app.services.ingress_auth import require_ingress

router = APIRouter(
    prefix="/provisioning/domains/{customer_id}/features",
    tags=["voice-features"],
    dependencies=[Depends(require_ingress)],
)


def _domain_name(db: Session, customer_id: str) -> str:
    dom = db.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == customer_id))
    if not dom:
        raise NotFoundError(f"No voice domain for customer {customer_id}")
    return dom.fusionpbx_domain


class ConferenceIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)


class RingGroupIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    members: list[str] = Field(min_length=1)
    strategy: str = "simultaneous"


class IvrIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    options: dict[str, str] = Field(min_length=1)
    greeting: str = "ivr/ivr-enter_ext_pound.wav"


class QueueIntent(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    agents: list[str] = Field(min_length=1)
    name: str | None = None
    strategy: str = "ring-all"


@router.post("/conferences")
def post_conference(
    customer_id: str,
    payload: ConferenceIntent,
    db: Session = Depends(get_db),
    client: FusionpbxClient = Depends(get_fusionpbx_client),
) -> dict:
    return client.create_conference(_domain_name(db, customer_id), payload.number)


@router.post("/ring-groups")
def post_ring_group(
    customer_id: str,
    payload: RingGroupIntent,
    db: Session = Depends(get_db),
    client: FusionpbxClient = Depends(get_fusionpbx_client),
) -> dict:
    return client.create_ring_group(
        _domain_name(db, customer_id),
        payload.number,
        payload.members,
        strategy=payload.strategy,
    )


@router.post("/ivrs")
def post_ivr(
    customer_id: str,
    payload: IvrIntent,
    db: Session = Depends(get_db),
    client: FusionpbxClient = Depends(get_fusionpbx_client),
) -> dict:
    return client.create_ivr(
        _domain_name(db, customer_id),
        payload.number,
        payload.options,
        greeting=payload.greeting,
    )


@router.post("/queues")
def post_queue(
    customer_id: str,
    payload: QueueIntent,
    db: Session = Depends(get_db),
    client: FusionpbxClient = Depends(get_fusionpbx_client),
) -> dict:
    return client.ensure_queue(
        _domain_name(db, customer_id),
        payload.number,
        agents=payload.agents,
        name=payload.name,
        strategy=payload.strategy,
    )
