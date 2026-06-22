"""CDR ingest service — maps mod_json_cdr payload to Cdr model."""
from datetime import UTC, datetime

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


def ingest_cdr(db: Session, payload: dict) -> Cdr:
    """Parse a mod_json_cdr payload and persist a Cdr row.

    FreeSWITCH wraps call data under a "variables" key; fall back to the
    top-level dict if that key is absent.
    """
    vars = payload.get("variables", payload)

    cdr = Cdr(
        call_uuid=vars["uuid"],
        customer_id=vars.get("variable_dotmac_subscriber_id") or vars.get("dotmac_subscriber_id"),
        direction=vars.get("direction", ""),
        caller=vars.get("caller_id_number", ""),
        callee=vars.get("destination_number", ""),
        duration_seconds=int(vars.get("duration", 0)),
        billsec=int(vars.get("billsec", 0)),
        hangup_cause=vars.get("hangup_cause", ""),
        start_at=_epoch_to_dt(vars.get("start_epoch")),
        answer_at=_epoch_to_dt(vars.get("answer_epoch")),
        end_at=_epoch_to_dt(vars.get("end_epoch")),
    )
    db.add(cdr)
    db.flush()
    return cdr
