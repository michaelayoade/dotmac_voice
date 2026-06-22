"""CDR ingest and feed endpoints."""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.voice import Cdr, CdrRatingStatus
from app.schemas.voice import CdrIngestResult, CdrRead
from app.services.cdr.ingest import ingest_cdr
from app.services.ingress_auth import require_ingress

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cdr",
    tags=["cdr"],
    dependencies=[Depends(require_ingress)],
)


def _commit(db: Session) -> None:
    db.commit()


@router.post("/ingest", response_model=CdrIngestResult, status_code=201)
def post_ingest(
    payload: dict,
    db: Session = Depends(get_db),
) -> CdrIngestResult:
    """Ingest a single mod_json_cdr JSON payload from FreeSWITCH."""
    cdr = ingest_cdr(db, payload)
    _commit(db)
    return CdrIngestResult(
        call_uuid=cdr.call_uuid,
        rating_status=cdr.rating_status.value,
    )


@router.get("", response_model=list[CdrRead])
def get_cdrs(
    rating_status: str = "raw",
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[CdrRead]:
    """Return CDRs filtered by rating_status, newest first."""
    try:
        status = CdrRatingStatus(rating_status)
    except ValueError:
        status = CdrRatingStatus.raw

    stmt = (
        select(Cdr)
        .where(Cdr.rating_status == status)
        .order_by(Cdr.created_at.desc())
        .limit(limit)
    )
    rows = list(db.scalars(stmt).all())
    return [
        CdrRead(
            id=row.id,
            call_uuid=row.call_uuid,
            customer_id=row.customer_id,
            direction=row.direction,
            caller=row.caller,
            callee=row.callee,
            start_at=row.start_at,
            answer_at=row.answer_at,
            end_at=row.end_at,
            duration_seconds=row.duration_seconds,
            billsec=row.billsec,
            hangup_cause=row.hangup_cause,
            recording_url=row.recording_url,
            rating_status=row.rating_status.value,
            created_at=row.created_at,
        )
        for row in rows
    ]
