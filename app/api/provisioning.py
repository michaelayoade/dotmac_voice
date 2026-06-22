import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.models.voice import Extension, VoiceDomain
from app.schemas.voice import DomainIntent, DomainSyncResult
from app.services.fusionpbx.client import FusionpbxClient
from app.services.ingress_auth import require_ingress
from app.services.reconcile.voice import reconcile_voice

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/provisioning",
    tags=["provisioning"],
    dependencies=[Depends(require_ingress)],
)


def get_fusionpbx_client() -> FusionpbxClient:
    return FusionpbxClient(settings.fusionpbx_db_url)


def _commit(db: Session) -> None:
    db.commit()


@router.put("/domains/{customer_id}", response_model=DomainSyncResult)
def put_domain(
    customer_id: str,
    payload: DomainIntent,
    db: Session = Depends(get_db),
    client: FusionpbxClient = Depends(get_fusionpbx_client),
) -> DomainSyncResult:
    domain = db.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == customer_id))
    if not domain:
        domain = VoiceDomain(
            customer_id=customer_id, fusionpbx_domain=payload.fusionpbx_domain
        )
        db.add(domain)
        db.flush()
    existing = {
        e.number: e
        for e in db.scalars(
            select(Extension).where(Extension.voice_domain_id == domain.id)
        )
    }

    # Desired state: replace extensions to match payload exactly
    payload_numbers = {ext.number for ext in payload.extensions}

    # Delete extensions not in payload
    for number, ext_obj in existing.items():
        if number not in payload_numbers:
            db.delete(ext_obj)

    # Add or update extensions from payload
    for ext in payload.extensions:
        if ext.number in existing:
            # Update display_name for existing extension
            existing[ext.number].display_name = ext.display_name
        else:
            # Add new extension
            db.add(
                Extension(
                    voice_domain_id=domain.id,
                    number=ext.number,
                    display_name=ext.display_name,
                )
            )
    db.flush()
    status = reconcile_voice(db, client, customer_id)
    _commit(db)
    return DomainSyncResult(customer_id=customer_id, sync_status=status.value)
