from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.provisioning import get_fusionpbx_client
from app.config import settings
from app.models.voice import (
    ConferenceRoom,
    Extension,
    IvrMenu,
    Queue,
    RingGroup,
    VoiceDomain,
)
from app.services import tokens as token_service
from app.services.exceptions import NotFoundError
from app.services.fusionpbx.client import FusionpbxClient
from app.services.ingress_auth import require_ingress

router = APIRouter(
    prefix="/tokens", tags=["tokens"], dependencies=[Depends(require_ingress)]
)


class TokenRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    scope: str = Field(min_length=1, max_length=120)
    ttl_seconds: int = Field(default=60, gt=0, le=300)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_token(payload: TokenRequest):
    return token_service.mint_token(payload.subject, payload.scope, payload.ttl_seconds)


class BootstrapRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    extension: str = Field(min_length=1, max_length=32)


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
def client_bootstrap(
    payload: BootstrapRequest,
    db: Session = Depends(get_db),
    client: FusionpbxClient = Depends(get_fusionpbx_client),
) -> dict:
    """Full WebRTC client bootstrap for one extension: SIP identity + auth, WSS
    endpoint, ICE/TURN config, an ephemeral JWT, and feature entitlements derived
    from what the customer actually has provisioned."""
    domain = db.scalar(
        select(VoiceDomain).where(VoiceDomain.customer_id == payload.customer_id)
    )
    if not domain:
        raise NotFoundError(f"No voice domain for customer {payload.customer_id}")
    secret = client.get_extension_secret(domain.fusionpbx_domain, payload.extension)
    if secret is None:
        raise NotFoundError(
            f"No extension {payload.extension} for customer {payload.customer_id}"
        )

    did = domain.id
    ext_model = db.scalar(
        select(Extension).where(
            Extension.voice_domain_id == did, Extension.number == payload.extension
        )
    )
    entitlements = {
        "voicemail": bool(ext_model and ext_model.voicemail_enabled),
        "conferences": sorted(
            c.number for c in db.scalars(
                select(ConferenceRoom).where(ConferenceRoom.voice_domain_id == did))
        ),
        "ring_groups": sorted(
            r.number for r in db.scalars(
                select(RingGroup).where(RingGroup.voice_domain_id == did))
        ),
        "ivrs": sorted(
            i.number for i in db.scalars(
                select(IvrMenu).where(IvrMenu.voice_domain_id == did))
        ),
        "queues": sorted(
            q.number for q in db.scalars(
                select(Queue).where(Queue.voice_domain_id == did))
        ),
    }
    minted = token_service.mint_token(payload.extension, "webrtc", 300)
    return {
        "sip": {
            "username": payload.extension,
            "domain": domain.fusionpbx_domain,
            "password": secret,
            "uri": f"sip:{payload.extension}@{domain.fusionpbx_domain}",
        },
        "wss_endpoint": settings.edge_wss_url,
        "ice_servers": token_service.build_ice_servers(),
        "token": minted["token"],
        "expires_in": minted["expires_in"],
        "entitlements": entitlements,
    }
