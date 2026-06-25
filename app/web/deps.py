"""Web authentication dependencies — cookie-based JWT auth."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.services.auth_flow import decode_access_token
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class WebAuthRedirect(HTTPException):
    """Raised when web auth fails — triggers redirect to login page."""

    def __init__(self, next_url: str = "/admin") -> None:
        self.next_url = next_url
        super().__init__(status_code=302, detail="Not authenticated")


def _make_aware(dt: datetime) -> datetime:
    if dt is None:
        return None  # type: ignore[return-value]
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def require_web_auth(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Read JWT from access_token cookie and validate session.

    Returns dict with person_id, session_id, roles, person.
    Raises WebAuthRedirect on failure.
    """
    token = request.cookies.get("access_token", "")
    if not token:
        raise WebAuthRedirect(next_url=request.url.path)

    try:
        payload = decode_access_token(db, token)
    except HTTPException:
        raise WebAuthRedirect(next_url=request.url.path)

    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise WebAuthRedirect(next_url=request.url.path)

    now = datetime.now(UTC)
    person_uuid = coerce_uuid(person_id)
    session_uuid = coerce_uuid(session_id)
    session = db.get(AuthSession, session_uuid)
    if (
        not session
        or session.person_id != person_uuid
        or session.status != SessionStatus.active
        or session.revoked_at is not None
        or _make_aware(session.expires_at) <= now
    ):
        raise WebAuthRedirect(next_url=request.url.path)

    person = db.get(Person, person_uuid)
    if not person:
        raise WebAuthRedirect(next_url=request.url.path)

    roles = list(
        db.scalars(
            select(Role.name)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .where(PersonRole.person_id == person_uuid)
            .where(Role.is_active.is_(True))
        ).all()
    )
    if "admin" not in roles:
        # Authenticated (valid token + session + person) but lacks the admin role:
        # this is a 403, not a redirect-to-login (which is for unauthenticated users).
        raise HTTPException(status_code=403, detail="Forbidden")

    return {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "roles": roles,
        "person": person,
    }
