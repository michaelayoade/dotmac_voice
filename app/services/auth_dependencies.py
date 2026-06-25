from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import ApiKey, SessionStatus
from app.models.auth import Session as AuthSession
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services.auth import hash_api_key
from app.services.auth_flow import decode_access_token, session_token_hash_candidates
from app.services.common import coerce_uuid


def _make_aware(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware (UTC). SQLite doesn't preserve tz info."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _is_jwt(token: str) -> bool:
    return token.count(".") == 2


def _person_has_audit_scope(db: Session, person_id: object) -> bool:
    person_uuid = coerce_uuid(str(person_id))
    role_names = set(
        db.scalars(
            select(Role.name)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .where(PersonRole.person_id == person_uuid)
            .where(Role.is_active.is_(True))
        ).all()
    )
    if role_names.intersection({"admin", "auditor"}):
        return True
    permission_keys = set(
        db.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .where(PersonRole.person_id == person_uuid)
            .where(Role.is_active.is_(True))
            .where(Permission.is_active.is_(True))
        ).all()
    )
    return bool(permission_keys.intersection({"audit:read", "audit:*"}))


def _person_has_role(db: Session, person_id: object, role_name: str) -> bool:
    person_uuid = coerce_uuid(str(person_id))
    return (
        db.scalars(
            select(PersonRole)
            .join(Role, PersonRole.role_id == Role.id)
            .where(PersonRole.person_id == person_uuid)
            .where(Role.name == role_name)
            .where(Role.is_active.is_(True))
            .limit(1)
        ).first()
        is not None
    )


def require_audit_auth(
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    request: Request = None,
    db: Session = Depends(get_db),
):
    token = _extract_bearer_token(authorization) or x_session_token
    now = datetime.now(UTC)
    if token:
        if _is_jwt(token):
            payload = decode_access_token(db, token)
            session_id = payload.get("session_id")
            if session_id:
                session = db.get(AuthSession, coerce_uuid(session_id))
                if not session:
                    raise HTTPException(status_code=401, detail="Invalid session")
                if session.status != SessionStatus.active or session.revoked_at:
                    raise HTTPException(status_code=401, detail="Invalid session")
                if _make_aware(session.expires_at) <= now:
                    raise HTTPException(status_code=401, detail="Session expired")
            actor_id = str(payload.get("sub"))
            if not _person_has_audit_scope(db, actor_id):
                raise HTTPException(status_code=403, detail="Insufficient scope")
            if request is not None:
                request.state.actor_id = actor_id
            return {"actor_type": "user", "actor_id": actor_id}
        session = db.scalars(
            select(AuthSession)
            .where(AuthSession.token_hash.in_(session_token_hash_candidates(token, db)))
            .where(AuthSession.status == SessionStatus.active)
            .where(AuthSession.revoked_at.is_(None))
            .where(AuthSession.expires_at > now)
            .limit(1)
        ).first()
        if session:
            if not _person_has_audit_scope(db, session.person_id):
                raise HTTPException(status_code=403, detail="Insufficient scope")
            if request is not None:
                request.state.actor_id = str(session.person_id)
            return {"actor_type": "user", "actor_id": str(session.person_id)}
    if x_api_key:
        api_key = db.scalars(
            select(ApiKey)
            .where(ApiKey.key_hash == hash_api_key(x_api_key))
            .where(ApiKey.is_active.is_(True))
            .where(ApiKey.revoked_at.is_(None))
            .where((ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now))
            .limit(1)
        ).first()
        if api_key:
            if not api_key.person_id or not _person_has_audit_scope(
                db, api_key.person_id
            ):
                raise HTTPException(status_code=403, detail="Insufficient scope")
            if request is not None:
                request.state.actor_id = str(api_key.id)
            return {"actor_type": "api_key", "actor_id": str(api_key.id)}
    raise HTTPException(status_code=401, detail="Unauthorized")


def resolve_active_session_person_id(db: Session, token: str) -> str | None:
    """Decode an access token AND confirm its backing session is still valid.

    Returns the person_id when the token decodes and the referenced
    ``AuthSession`` is active (not revoked, not expired); otherwise ``None``.
    Shared by HTTP and WebSocket auth so that logout / password reset /
    session revocation immediately invalidate live connections instead of
    leaving them open until JWT expiry.
    """
    if not token:
        return None
    try:
        payload = decode_access_token(db, token)
    except Exception:
        return None
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        return None
    now = datetime.now(UTC)
    session = db.scalars(
        select(AuthSession)
        .where(AuthSession.id == coerce_uuid(session_id))
        .where(AuthSession.person_id == coerce_uuid(person_id))
        .where(AuthSession.status == SessionStatus.active)
        .where(AuthSession.revoked_at.is_(None))
        .where(AuthSession.expires_at > now)
        .limit(1)
    ).first()
    if not session:
        return None
    return str(person_id)


def require_user_auth(
    authorization: str | None = Header(default=None),
    request: Request = None,
    db: Session = Depends(get_db),
):
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    payload = decode_access_token(db, token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now(UTC)
    person_uuid = coerce_uuid(person_id)
    session_uuid = coerce_uuid(session_id)
    session = db.scalars(
        select(AuthSession)
        .where(AuthSession.id == session_uuid)
        .where(AuthSession.person_id == person_uuid)
        .where(AuthSession.status == SessionStatus.active)
        .where(AuthSession.revoked_at.is_(None))
        .where(AuthSession.expires_at > now)
        .limit(1)
    ).first()
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    roles_value = payload.get("roles")
    scopes_value = payload.get("scopes")
    roles = [str(role) for role in roles_value] if isinstance(roles_value, list) else []
    scopes = (
        [str(scope) for scope in scopes_value] if isinstance(scopes_value, list) else []
    )
    actor_id = str(person_id)
    if request is not None:
        request.state.actor_id = actor_id
    return {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "roles": roles,
        "scopes": scopes,
    }


def require_role(role_name: str):
    def _require_role(
        auth=Depends(require_user_auth),
        db: Session = Depends(get_db),
    ):
        person_id = coerce_uuid(auth["person_id"])
        role = db.scalars(
            select(Role)
            .where(Role.name == role_name)
            .where(Role.is_active.is_(True))
            .limit(1)
        ).first()
        if not role:
            raise HTTPException(status_code=403, detail="Role not found")
        link = db.scalars(
            select(PersonRole)
            .where(PersonRole.person_id == person_id)
            .where(PersonRole.role_id == role.id)
            .limit(1)
        ).first()
        if not link:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_role


def require_permission(permission_key: str):
    def _require_permission(
        auth=Depends(require_user_auth),
        db: Session = Depends(get_db),
    ):
        person_id = coerce_uuid(auth["person_id"])
        if _person_has_role(db, person_id, "admin"):
            return auth
        permission = db.scalars(
            select(Permission)
            .where(Permission.key == permission_key)
            .where(Permission.is_active.is_(True))
            .limit(1)
        ).first()
        if not permission:
            raise HTTPException(status_code=403, detail="Permission not found")
        has_permission = db.scalars(
            select(RolePermission)
            .join(Role, RolePermission.role_id == Role.id)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .where(PersonRole.person_id == person_id)
            .where(RolePermission.permission_id == permission.id)
            .where(Role.is_active.is_(True))
            .limit(1)
        ).first()
        if not has_permission:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _require_permission
