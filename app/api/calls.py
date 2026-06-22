"""Click-to-dial API endpoint.

CRM posts an agent extension + destination; this endpoint runs the fraud policy
and, if allowed, tells FreeSWITCH (via ESL) to originate a bridged call.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.services.freeswitch.esl import EslBridge, build_originate_command
from app.services.ingress_auth import require_ingress
from app.services.routing.fraud import DialPolicy, check_dial

router = APIRouter(
    prefix="/calls",
    tags=["calls"],
    dependencies=[Depends(require_ingress)],
)


def get_esl_bridge() -> EslBridge:
    """Construct an EslBridge from settings; override in tests via dependency_overrides."""
    return EslBridge(
        host=settings.esl_host,
        port=settings.esl_port,
        password=settings.esl_password,
    )


class DialRequest(BaseModel):
    agent_extension: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    destination: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9+*#_.-]+$",
    )
    domain: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9.-]+$",
    )
    caller_id_number: str = Field(
        default="",
        max_length=32,
        pattern=r"^[0-9+]*$",
    )
    allow_international: bool = False
    allowed_destinations: list[str] | None = None
    blocked_prefixes: list[str] = []


def _strip_formatting(number: str) -> str:
    """Remove whitespace, dashes, and parens from a number string for the originate command.

    Trust-boundary note: a bare international number with no '+' or '00' marker is treated
    as domestic by the fraud module's normalizer. Robust E.164 canonicalization (e.g. via
    the `phonenumbers` library) at this boundary is a tracked follow-up.
    """
    return re.sub(r"[\s\-()]", "", number)


@router.post("/dial")
def dial(
    payload: DialRequest,
    esl: EslBridge = Depends(get_esl_bridge),
) -> dict:
    """Originate a bridged call from an agent extension to a destination.

    Runs fraud policy first; if denied, returns 403 with the denial reason.
    If allowed, sends an ESL originate command and returns status + classification.
    """
    # Strip formatting whitespace/dashes/parens for the originate command.
    clean_destination = _strip_formatting(payload.destination)

    policy = DialPolicy(
        allowed_destinations=tuple(payload.allowed_destinations)
        if payload.allowed_destinations is not None
        else None,
        allow_international=payload.allow_international,
        blocked_prefixes=tuple(payload.blocked_prefixes),
    )

    decision = check_dial(payload.destination, policy)

    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=decision.reason,
        )

    command = build_originate_command(
        agent_extension=payload.agent_extension,
        destination=clean_destination,
        domain=payload.domain,
        caller_id_number=payload.caller_id_number,
    )

    esl.originate(command)

    return {
        "status": "originating",
        "classification": decision.classification,
    }
