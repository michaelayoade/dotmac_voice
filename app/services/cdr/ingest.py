"""CDR ingest service — maps mod_json_cdr payload to Cdr model."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.voice import Cdr


def _epoch_to_dt(value: str | int | None) -> datetime | None:
    """Convert an epoch-seconds value to a timezone-aware datetime, or None."""
    if value is None:
        return None
    try:
        epoch = int(value)
    except (ValueError, TypeError):
        return None
    if epoch == 0:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC)


def _to_int(value: object) -> int:
    """Coerce a value to int, returning 0 on any conversion failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def ingest_cdr(db: Session, payload: dict) -> Cdr:
    """Parse a mod_json_cdr payload and persist a Cdr row.

    FreeSWITCH wraps call data under a "variables" key; fall back to the
    top-level dict if that key is absent.
    """
    vars = payload.get("variables", payload)
    call_uuid = vars.get("uuid", "")

    fields = dict(
        customer_id=vars.get("variable_dotmac_subscriber_id")
        or vars.get("dotmac_subscriber_id"),
        direction=vars.get("direction", ""),
        caller=vars.get("caller_id_number", ""),
        callee=vars.get("destination_number", ""),
        duration_seconds=_to_int(vars.get("duration")),
        billsec=_to_int(vars.get("billsec")),
        hangup_cause=vars.get("hangup_cause", ""),
        start_at=_epoch_to_dt(vars.get("start_epoch")),
        answer_at=_epoch_to_dt(vars.get("answer_epoch")),
        end_at=_epoch_to_dt(vars.get("end_epoch")),
        recording_url=vars.get("variable_recording_file") or vars.get("recording_file"),
    )

    # Idempotent upsert by call_uuid: mod_json_cdr (or its retry) may deliver the
    # same call more than once; update in place rather than creating duplicates.
    cdr = (
        db.scalar(select(Cdr).where(Cdr.call_uuid == call_uuid)) if call_uuid else None
    )
    if cdr is None:
        cdr = Cdr(call_uuid=call_uuid, **fields)
        db.add(cdr)
    else:
        for key, value in fields.items():
            setattr(cdr, key, value)
    db.flush()
    return cdr
