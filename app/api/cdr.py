"""CDR ingest and feed endpoints."""
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.voice import Cdr, CdrRatingStatus
from app.schemas.voice import CdrIngestResult, CdrRead
from app.services.cdr.ingest import ingest_cdr
from app.services.exceptions import BadRequestError
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
    customer_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[CdrRead]:
    """Return CDRs newest first. With ``customer_id`` -> that customer's call history
    (all rating statuses); otherwise the rating-status feed (default raw)."""
    stmt = select(Cdr).order_by(Cdr.created_at.desc()).limit(limit)
    if customer_id is not None:
        stmt = stmt.where(Cdr.customer_id == customer_id)
    else:
        try:
            status = CdrRatingStatus(rating_status)
        except ValueError:
            status = CdrRatingStatus.raw
        stmt = stmt.where(Cdr.rating_status == status)
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


class CdrMarkRequest(BaseModel):
    call_uuids: list[str]
    rating_status: str


@router.post("/mark")
def mark_cdrs(
    payload: CdrMarkRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Transition rating state (raw -> rated -> fed) for a batch of CDRs by call_uuid.
    The billing pipeline rates raw CDRs then marks them fed once exported."""
    try:
        status = CdrRatingStatus(payload.rating_status)
    except ValueError as exc:
        raise BadRequestError(
            f"invalid rating_status: {payload.rating_status}"
        ) from exc
    rows = list(db.scalars(select(Cdr).where(Cdr.call_uuid.in_(payload.call_uuids))))
    for cdr in rows:
        cdr.rating_status = status
    db.commit()
    return {"marked": len(rows), "rating_status": status.value}
