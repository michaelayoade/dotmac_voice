from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, Response, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.auth import (
    AuthProvider,
    MFAMethod,
    MFAMethodType,
    SessionStatus,
    UserCredential,
)
from app.models.auth import (
    Session as AuthSession,
)
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.schemas.auth_flow import (
    AvatarUploadResponse,
    LoginResponse,
    LogoutResponse,
    MeResponse,
    PasswordChangeResponse,
    SessionInfoResponse,
    SessionListResponse,
    SessionRevokeResponse,
    TokenResponse,
)
from app.services import avatar as avatar_service
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin
from app.services.secrets import resolve_secret

logger = logging.getLogger(__name__)

PASSWORD_CONTEXT = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    default="pbkdf2_sha256",
    deprecated="auto",
)

_DUMMY_PASSWORD_HASH = PASSWORD_CONTEXT.hash("dummy-password")


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _env_int(name: str) -> int | None:
    raw = _env_value(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _truncate_user_agent(value: str | None, max_len: int = 512) -> str | None:
    if not value:
        return value
    if len(value) <= max_len:
        return value
    return value[:max_len]


def _setting_value(db: Session | None, key: str) -> str | None:
    if db is None:
        return None
    setting = db.scalars(
        select(DomainSetting)
        .where(DomainSetting.domain == SettingDomain.auth)
        .where(DomainSetting.key == key)
        .where(DomainSetting.is_active.is_(True))
        .limit(1)
    ).first()
    if not setting:
        return None
    if setting.value_text:
        return setting.value_text
    if setting.value_json is not None:
        return str(setting.value_json)
    return None


def _jwt_secret(db: Session | None) -> str:
    secret = _env_value("JWT_SECRET") or _setting_value(db, "jwt_secret")
    secret = resolve_secret(secret)
    if not secret:
        raise HTTPException(status_code=500, detail="JWT secret not configured")
    return secret


def _jwt_algorithm(db: Session | None) -> str:
    return _env_value("JWT_ALGORITHM") or _setting_value(db, "jwt_algorithm") or "HS256"


def _access_ttl_minutes(db: Session | None) -> int:
    env_value = _env_int("JWT_ACCESS_TTL_MINUTES")
    if env_value is not None:
        return env_value
    value = _setting_value(db, "jwt_access_ttl_minutes")
    if value is not None:
        try:
            return int(value)
        except ValueError:
            return 15
    return 15


def _refresh_ttl_days(db: Session | None) -> int:
    env_value = _env_int("JWT_REFRESH_TTL_DAYS")
    if env_value is not None:
        return env_value
    value = _setting_value(db, "jwt_refresh_ttl_days")
    if value is not None:
        try:
            return int(value)
        except ValueError:
            return 30
    return 30


def _totp_issuer(db: Session | None) -> str:
    return (
        _env_value("TOTP_ISSUER")
        or _setting_value(db, "totp_issuer")
        or "dotmac_voice"
    )


def _refresh_cookie_name(db: Session | None) -> str:
    return (
        _env_value("REFRESH_COOKIE_NAME")
        or _setting_value(db, "refresh_cookie_name")
        or "refresh_token"
    )


def _refresh_cookie_secure(db: Session | None) -> bool:
    env_value = _env_value("REFRESH_COOKIE_SECURE")
    if env_value is not None:
        return env_value.lower() in {"1", "true", "yes", "on"}
    value = _setting_value(db, "refresh_cookie_secure")
    if value is not None:
        return str(value).lower() in {"1", "true", "yes", "on"}
    return True


def _refresh_cookie_samesite(db: Session | None) -> str:
    return (
        _env_value("REFRESH_COOKIE_SAMESITE")
        or _setting_value(db, "refresh_cookie_samesite")
        or "lax"
    )


def _refresh_cookie_domain(db: Session | None) -> str | None:
    return _env_value("REFRESH_COOKIE_DOMAIN") or _setting_value(
        db, "refresh_cookie_domain"
    )


def _refresh_cookie_path(db: Session | None) -> str:
    return (
        _env_value("REFRESH_COOKIE_PATH")
        or _setting_value(db, "refresh_cookie_path")
        or "/"
    )


def _mfa_key(db: Session | None) -> bytes:
    key = _env_value("TOTP_ENCRYPTION_KEY") or _setting_value(db, "totp_encryption_key")
    key = resolve_secret(key)
    if not key:
        raise HTTPException(
            status_code=500, detail="TOTP encryption key not configured"
        )
    return key.encode()


def _fernet(db: Session | None) -> Fernet:
    try:
        return Fernet(_mfa_key(db))
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="Invalid TOTP encryption key"
        ) from exc


def _legacy_hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_hash_secret(db: Session | None = None) -> str:
    secret = (
        _env_value("SESSION_TOKEN_HASH_SECRET")
        or _env_value("API_KEY_HASH_SECRET")
        or _env_value("JWT_SECRET")
        or _env_value("SECRET_KEY")
        or _setting_value(db, "session_token_hash_secret")
        or _setting_value(db, "jwt_secret")
    )
    secret = resolve_secret(secret)
    if not secret:
        raise HTTPException(status_code=500, detail="Session hash secret not configured")
    return secret


def _hash_token(token: str, db: Session | None = None) -> str:
    secret = _session_hash_secret(db)
    return hmac.new(
        secret.encode("utf-8"),
        token.encode("utf-8"),
        "sha256",
    ).hexdigest()


def hash_session_token(token: str, db: Session | None = None) -> str:
    return _hash_token(token, db)


def session_token_hash_candidates(
    token: str, db: Session | None = None
) -> tuple[str, ...]:
    current = hash_session_token(token, db)
    legacy = _legacy_hash_token(token)
    return (current, legacy) if current != legacy else (current,)


def _issue_access_token(
    db: Session | None,
    person_id: str,
    session_id: str,
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
) -> str:
    now = _now()
    payload = {
        "sub": person_id,
        "session_id": session_id,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_access_ttl_minutes(db))).timestamp()),
    }
    if roles:
        payload["roles"] = roles
    if permissions:
        payload["scopes"] = permissions
    return jwt.encode(payload, _jwt_secret(db), algorithm=_jwt_algorithm(db))


def _issue_mfa_token(db: Session | None, person_id: str) -> str:
    now = _now()
    payload = {
        "sub": person_id,
        "typ": "mfa",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(db), algorithm=_jwt_algorithm(db))


def _password_reset_ttl_minutes(db: Session | None) -> int:
    env_value = _env_int("PASSWORD_RESET_TTL_MINUTES")
    if env_value is not None:
        return env_value
    value = _setting_value(db, "password_reset_ttl_minutes")
    if value is not None:
        try:
            return int(value)
        except ValueError:
            return 60
    return 60


def _issue_password_reset_token(db: Session | None, person_id: str, email: str) -> str:
    now = _now()
    payload = {
        "sub": person_id,
        "email": email,
        "typ": "password_reset",
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=_password_reset_ttl_minutes(db))).timestamp()
        ),
    }
    return jwt.encode(payload, _jwt_secret(db), algorithm=_jwt_algorithm(db))


def _decode_password_reset_token(db: Session | None, token: str) -> dict:
    return _decode_jwt(db, token, "password_reset")


def _decode_jwt(db: Session | None, token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, _jwt_secret(db), algorithms=[_jwt_algorithm(db)])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if payload.get("typ") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload


def decode_access_token(db: Session | None, token: str) -> dict:
    return _decode_jwt(db, token, "access")


def _person_or_404(db: Session, person_id: str) -> Person:
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def _load_rbac_claims(db: Session, person_id: str):
    if db is None:
        return [], []
    person_uuid = coerce_uuid(person_id)
    roles = db.scalars(
        select(Role)
        .join(PersonRole, PersonRole.role_id == Role.id)
        .where(PersonRole.person_id == person_uuid)
        .where(Role.is_active.is_(True))
    ).all()
    permissions = db.scalars(
        select(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, RolePermission.role_id == Role.id)
        .join(PersonRole, PersonRole.role_id == Role.id)
        .where(PersonRole.person_id == person_uuid)
        .where(Role.is_active.is_(True))
        .where(Permission.is_active.is_(True))
    ).all()
    role_names = [role.name for role in roles]
    permission_keys = list({perm.key for perm in permissions})
    return role_names, permission_keys


def _primary_totp_method(
    db: Session, person_id: str, *, for_update: bool = False
) -> MFAMethod | None:
    statement = (
        select(MFAMethod)
        .where(MFAMethod.person_id == coerce_uuid(person_id))
        .where(MFAMethod.method_type == MFAMethodType.totp)
        .where(MFAMethod.is_active.is_(True))
        .where(MFAMethod.enabled.is_(True))
        .where(MFAMethod.is_primary.is_(True))
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalars(statement).first()


def _verify_totp_once(method: MFAMethod, secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    now = _now()
    counter = totp.timecode(now)
    if not totp.verify(code, for_time=now, valid_window=0):
        return False
    if method.last_totp_counter is not None and counter <= method.last_totp_counter:
        return False
    method.last_totp_counter = counter
    return True


def _encrypt_secret(db: Session | None, secret: str) -> str:
    return _fernet(db).encrypt(secret.encode("utf-8")).decode("utf-8")


def _decrypt_secret(db: Session | None, secret: str) -> str:
    try:
        return _fernet(db).decrypt(secret.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Invalid MFA secret") from exc


def hash_password(password: str) -> str:
    return PASSWORD_CONTEXT.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return PASSWORD_CONTEXT.verify(password, password_hash)


def revoke_sessions_for_person(
    db: Session,
    person_id: str,
    exclude_session_id: str | None = None,
) -> int:
    person_uuid = coerce_uuid(person_id)
    statement = (
        select(AuthSession)
        .where(AuthSession.person_id == person_uuid)
        .where(AuthSession.status == SessionStatus.active)
        .where(AuthSession.revoked_at.is_(None))
    )
    if exclude_session_id:
        statement = statement.where(AuthSession.id != coerce_uuid(exclude_session_id))

    sessions = db.scalars(statement).all()
    if not sessions:
        return 0

    now = _now()
    for session in sessions:
        session.status = SessionStatus.revoked
        session.revoked_at = now
    return len(sessions)


def _me_response(person: Person, auth: dict) -> MeResponse:
    return MeResponse(
        id=person.id,
        first_name=person.first_name,
        last_name=person.last_name,
        display_name=person.display_name,
        avatar_url=person.avatar_url,
        email=person.email,
        email_verified=person.email_verified,
        phone=person.phone,
        date_of_birth=person.date_of_birth,
        gender=person.gender.value if person.gender else "unknown",
        preferred_contact_method=person.preferred_contact_method.value
        if person.preferred_contact_method
        else None,
        locale=person.locale,
        timezone=person.timezone,
        roles=auth.get("roles", []),
        scopes=auth.get("scopes", []),
    )


class AuthFlow(ListResponseMixin):
    @staticmethod
    def _response_with_refresh_cookie(
        db: Session | None,
        payload: dict,
        model_cls,
        status_code: int = status.HTTP_200_OK,
    ) -> Response:
        settings = AuthFlow.refresh_cookie_settings(db)
        response = Response(status_code=status_code)
        response.set_cookie(
            key=settings["key"],
            value=payload["refresh_token"],
            httponly=settings["httponly"],
            secure=settings["secure"],
            samesite=settings["samesite"],
            domain=settings["domain"],
            path=settings["path"],
            max_age=settings["max_age"],
        )
        response.media_type = "application/json"
        payload = {**payload, "refresh_token": None}
        response.body = model_cls(**payload).model_dump_json().encode("utf-8")
        return response

    @staticmethod
    def _response_clear_refresh_cookie(
        db: Session | None,
        payload: dict,
        model_cls,
        status_code: int = status.HTTP_200_OK,
    ) -> Response:
        settings = AuthFlow.refresh_cookie_settings(db)
        response = Response(status_code=status_code)
        response.delete_cookie(
            key=settings["key"],
            domain=settings["domain"],
            path=settings["path"],
        )
        response.media_type = "application/json"
        response.body = model_cls(**payload).model_dump_json().encode("utf-8")
        return response

    @staticmethod
    def login_response(
        db: Session,
        username: str,
        password: str,
        request: Request,
        provider: str | None,
    ):
        result = AuthFlow.login(db, username, password, request, provider)
        if result.get("refresh_token"):
            return AuthFlow._response_with_refresh_cookie(
                db, result, LoginResponse, status.HTTP_200_OK
            )
        return result

    @staticmethod
    def login(
        db: Session,
        username: str,
        password: str,
        request: Request,
        provider: str | None,
    ):
        if isinstance(provider, AuthProvider):
            provider_value = provider.value
        else:
            provider_value = provider or AuthProvider.local.value
        try:
            resolved_provider = AuthProvider(provider_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid auth provider"
            ) from exc
        credential = db.scalars(
            select(UserCredential)
            .where(UserCredential.username == username)
            .where(UserCredential.provider == resolved_provider)
            .where(UserCredential.is_active.is_(True))
            .limit(1)
        ).first()
        if not credential:
            verify_password(password, _DUMMY_PASSWORD_HASH)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        now = _now()
        if credential.locked_until and credential.locked_until > now:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not verify_password(password, credential.password_hash):
            credential.failed_login_attempts += 1
            if credential.failed_login_attempts >= 5:
                credential.locked_until = now + timedelta(minutes=15)
            db.flush()
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if credential.must_change_password:
            raise HTTPException(
                status_code=428,
                detail={
                    "code": "PASSWORD_RESET_REQUIRED",
                    "message": "Password reset required",
                },
            )

        credential.failed_login_attempts = 0
        credential.locked_until = None
        credential.last_login_at = now
        db.flush()

        if _primary_totp_method(db, str(credential.person_id)):
            return {
                "mfa_required": True,
                "mfa_token": _issue_mfa_token(db, str(credential.person_id)),
            }

        return AuthFlow._issue_tokens(db, credential.person_id, request)

    @staticmethod
    def mfa_setup(db: Session, person_id: str, label: str | None):
        person = _person_or_404(db, person_id)
        username = person.email
        credential = db.scalars(
            select(UserCredential)
            .where(UserCredential.person_id == person.id)
            .where(UserCredential.provider == AuthProvider.local)
            .limit(1)
        ).first()
        if credential and credential.username:
            username = credential.username

        secret = pyotp.random_base32()
        encrypted = _encrypt_secret(db, secret)
        method = MFAMethod(
            person_id=person.id,
            method_type=MFAMethodType.totp,
            label=label,
            secret=encrypted,
            enabled=False,
            is_primary=False,
        )
        db.add(method)
        db.flush()
        db.refresh(method)

        totp = pyotp.TOTP(secret)
        otpauth_uri = totp.provisioning_uri(name=username, issuer_name=_totp_issuer(db))
        return {"method_id": method.id, "secret": secret, "otpauth_uri": otpauth_uri}

    @staticmethod
    def mfa_confirm(
        db: Session,
        method_id: str,
        code: str,
        expected_person_id: str | None = None,
    ):
        method = db.scalars(
            select(MFAMethod)
            .where(MFAMethod.id == coerce_uuid(method_id))
            .with_for_update()
            .limit(1)
        ).first()
        if not method:
            raise HTTPException(status_code=404, detail="MFA method not found")
        if expected_person_id:
            expected_uuid = coerce_uuid(expected_person_id)
            if method.person_id != expected_uuid:
                raise HTTPException(status_code=404, detail="MFA method not found")
        if method.method_type != MFAMethodType.totp:
            raise HTTPException(status_code=400, detail="Unsupported MFA method")

        secret = _decrypt_secret(db, method.secret or "")
        if not _verify_totp_once(method, secret, code):
            raise HTTPException(status_code=401, detail="Invalid MFA code")

        db.execute(
            update(MFAMethod)
            .where(
                MFAMethod.person_id == method.person_id,
                MFAMethod.id != method.id,
                MFAMethod.is_primary.is_(True),
            )
            .values(is_primary=False)
        )

        method.enabled = True
        method.is_primary = True
        method.is_active = True
        method.verified_at = _now()
        try:
            db.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="Primary MFA method already exists for this user",
            ) from exc
        db.refresh(method)
        return method

    @staticmethod
    def mfa_verify(db: Session, mfa_token: str, code: str, request: Request):
        payload = _decode_jwt(db, mfa_token, "mfa")
        person_id = payload.get("sub")
        if not person_id:
            raise HTTPException(status_code=401, detail="Invalid MFA token")

        method = _primary_totp_method(db, str(person_id), for_update=True)
        if not method:
            raise HTTPException(status_code=404, detail="MFA method not found")

        secret = _decrypt_secret(db, method.secret or "")
        if not _verify_totp_once(method, secret, code):
            raise HTTPException(status_code=401, detail="Invalid MFA code")

        method.last_used_at = _now()
        db.flush()
        return AuthFlow._issue_tokens(db, person_id, request)

    @staticmethod
    def mfa_verify_response(db: Session, mfa_token: str, code: str, request: Request):
        result = AuthFlow.mfa_verify(db, mfa_token, code, request)
        return AuthFlow._response_with_refresh_cookie(
            db, result, TokenResponse, status.HTTP_200_OK
        )

    @staticmethod
    def refresh(db: Session, refresh_token: str, request: Request):
        token_hashes = session_token_hash_candidates(refresh_token, db)
        session = db.scalars(
            select(AuthSession)
            .where(AuthSession.token_hash.in_(token_hashes))
            .where(AuthSession.status == SessionStatus.active)
            .where(AuthSession.revoked_at.is_(None))
            .with_for_update()
            .limit(1)
        ).first()
        if not session:
            reused = db.scalars(
                select(AuthSession)
                .where(AuthSession.previous_token_hash.in_(token_hashes))
                .where(AuthSession.status == SessionStatus.active)
                .where(AuthSession.revoked_at.is_(None))
                .with_for_update()
                .limit(1)
            ).first()
            if reused:
                if len(token_hashes) > 1 and reused.previous_token_hash == token_hashes[1]:
                    logger.warning(
                        "Matched legacy previous refresh-token hash for session %s",
                        reused.id,
                    )
                reused.status = SessionStatus.revoked
                reused.revoked_at = _now()
                db.flush()
                raise HTTPException(
                    status_code=401,
                    detail="Refresh token reuse detected",
            )
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        if len(token_hashes) > 1 and session.token_hash == token_hashes[1]:
            logger.warning(
                "Matched legacy refresh-token hash for session %s", session.id
            )
        expires_at = _as_utc(session.expires_at)
        if expires_at and expires_at <= _now():
            session.status = SessionStatus.expired
            db.flush()
            raise HTTPException(status_code=401, detail="Refresh token expired")

        new_refresh = secrets.token_urlsafe(48)
        session.previous_token_hash = session.token_hash
        session.token_hash = _hash_token(new_refresh, db)
        session.token_rotated_at = _now()
        session.last_seen_at = _now()
        if request.client:
            session.ip_address = request.client.host
        session.user_agent = _truncate_user_agent(request.headers.get("user-agent"))
        db.flush()

        roles, permissions = _load_rbac_claims(db, str(session.person_id))
        access_token = _issue_access_token(
            db, str(session.person_id), str(session.id), roles, permissions
        )
        return {"access_token": access_token, "refresh_token": new_refresh}

    @staticmethod
    def refresh_response(db: Session, refresh_token: str | None, request: Request):
        resolved = AuthFlow.resolve_refresh_token(request, refresh_token, db)
        if not resolved:
            raise HTTPException(status_code=401, detail="Missing refresh token")
        result = AuthFlow.refresh(db, resolved, request)
        return AuthFlow._response_with_refresh_cookie(
            db, result, TokenResponse, status.HTTP_200_OK
        )

    @staticmethod
    def logout(db: Session, refresh_token: str):
        token_hashes = session_token_hash_candidates(refresh_token, db)
        session = db.scalars(
            select(AuthSession)
            .where(AuthSession.token_hash.in_(token_hashes))
            .where(AuthSession.revoked_at.is_(None))
            .limit(1)
        ).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session.status = SessionStatus.revoked
        session.revoked_at = _now()
        db.flush()
        return {"revoked_at": session.revoked_at}

    @staticmethod
    def logout_response(db: Session, refresh_token: str | None, request: Request):
        resolved = AuthFlow.resolve_refresh_token(request, refresh_token, db)
        if not resolved:
            raise HTTPException(status_code=404, detail="Session not found")
        result = AuthFlow.logout(db, resolved)
        return AuthFlow._response_clear_refresh_cookie(
            db, result, LogoutResponse, status.HTTP_200_OK
        )

    @staticmethod
    def resolve_refresh_token(
        request: Request, refresh_token: str | None, db: Session | None = None
    ):
        settings = AuthFlow.refresh_cookie_settings(db)
        return refresh_token or request.cookies.get(settings["key"])

    @staticmethod
    def refresh_cookie_settings(db: Session | None = None):
        return {
            "key": _refresh_cookie_name(db),
            "httponly": True,
            "secure": _refresh_cookie_secure(db),
            "samesite": _refresh_cookie_samesite(db),
            "domain": _refresh_cookie_domain(db),
            "path": _refresh_cookie_path(db),
            "max_age": _refresh_ttl_days(db) * 24 * 60 * 60,
        }

    @staticmethod
    def me_response(db: Session, auth: dict) -> MeResponse:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")
        return _me_response(person, auth)

    @staticmethod
    def update_me_response(db: Session, auth: dict, payload) -> MeResponse:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(person, field, value)
        db.flush()
        return _me_response(person, auth)

    @staticmethod
    async def upload_avatar_response(db: Session, auth: dict, file) -> AvatarUploadResponse:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")
        avatar_service.delete_avatar(person.avatar_url)
        person.avatar_url = await avatar_service.save_avatar(file, str(person.id))
        db.flush()
        return AvatarUploadResponse(avatar_url=person.avatar_url)

    @staticmethod
    def delete_avatar(db: Session, auth: dict) -> None:
        person = db.get(Person, coerce_uuid(auth["person_id"]))
        if not person:
            raise HTTPException(status_code=404, detail="User not found")
        avatar_service.delete_avatar(person.avatar_url)
        person.avatar_url = None
        db.flush()

    @staticmethod
    def list_sessions_response(db: Session, auth: dict) -> SessionListResponse:
        person_id = coerce_uuid(auth["person_id"])
        sessions = db.scalars(
            select(AuthSession)
            .where(AuthSession.person_id == person_id)
            .where(AuthSession.status == SessionStatus.active)
            .where(AuthSession.revoked_at.is_(None))
            .order_by(AuthSession.created_at.desc())
        ).all()
        current_session_id = auth.get("session_id")
        return SessionListResponse(
            sessions=[
                SessionInfoResponse(
                    id=s.id,
                    status=s.status.value,
                    ip_address=s.ip_address,
                    user_agent=s.user_agent,
                    created_at=s.created_at,
                    last_seen_at=s.last_seen_at,
                    expires_at=s.expires_at,
                    is_current=(str(s.id) == current_session_id),
                )
                for s in sessions
            ],
            total=len(sessions),
        )

    @staticmethod
    def revoke_session_response(
        db: Session, auth: dict, session_id: str
    ) -> SessionRevokeResponse:
        session = db.scalars(
            select(AuthSession)
            .where(AuthSession.id == coerce_uuid(session_id))
            .where(AuthSession.person_id == coerce_uuid(auth["person_id"]))
            .limit(1)
        ).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status == SessionStatus.revoked:
            raise HTTPException(status_code=400, detail="Session already revoked")
        now = _now()
        session.status = SessionStatus.revoked
        session.revoked_at = now
        db.flush()
        return SessionRevokeResponse(revoked_at=now)

    @staticmethod
    def revoke_all_other_sessions_response(
        db: Session, auth: dict
    ) -> SessionRevokeResponse:
        current_session_id = auth.get("session_id")
        current_uuid = coerce_uuid(current_session_id) if current_session_id else None
        sessions = db.scalars(
            select(AuthSession)
            .where(AuthSession.person_id == coerce_uuid(auth["person_id"]))
            .where(AuthSession.status == SessionStatus.active)
            .where(AuthSession.revoked_at.is_(None))
            .where(AuthSession.id != current_uuid)
        ).all()
        now = _now()
        for session in sessions:
            session.status = SessionStatus.revoked
            session.revoked_at = now
        db.flush()
        return SessionRevokeResponse(revoked_at=now, revoked_count=len(sessions))

    @staticmethod
    def change_password_response(db: Session, auth: dict, payload) -> PasswordChangeResponse:
        credential = db.scalars(
            select(UserCredential)
            .where(UserCredential.person_id == coerce_uuid(auth["person_id"]))
            .where(UserCredential.is_active.is_(True))
            .limit(1)
        ).first()
        if not credential:
            raise HTTPException(status_code=404, detail="No credentials found")
        if not verify_password(payload.current_password, credential.password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect")
        if payload.current_password == payload.new_password:
            raise HTTPException(status_code=400, detail="New password must be different")
        now = _now()
        credential.password_hash = hash_password(payload.new_password)
        credential.password_updated_at = now
        credential.must_change_password = False
        revoke_sessions_for_person(db, auth["person_id"])
        db.flush()
        return PasswordChangeResponse(changed_at=now)

    @staticmethod
    def _issue_tokens(db: Session, person_id: str, request: Request):
        person_uuid = coerce_uuid(person_id)
        refresh_token = secrets.token_urlsafe(48)
        now = _now()
        expires_at = now + timedelta(days=_refresh_ttl_days(db))
        session = AuthSession(
            person_id=person_uuid,
            status=SessionStatus.active,
            token_hash=_hash_token(refresh_token, db),
            ip_address=request.client.host if request.client else None,
            user_agent=_truncate_user_agent(request.headers.get("user-agent")),
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
        db.add(session)
        db.flush()
        db.refresh(session)
        roles, permissions = _load_rbac_claims(db, str(person_uuid))
        access_token = _issue_access_token(
            db, str(person_uuid), str(session.id), roles, permissions
        )
        return {"access_token": access_token, "refresh_token": refresh_token}


auth_flow = AuthFlow()


def request_password_reset(db: Session, email: str) -> dict | None:
    """
    Request a password reset for the given email.
    Returns dict with token and person info if successful, None if email not found.
    Does not raise an error if email doesn't exist (security best practice).
    """
    person = db.scalars(select(Person).where(Person.email == email).limit(1)).first()
    if not person:
        return None

    credential = db.scalars(
        select(UserCredential)
        .where(UserCredential.person_id == person.id)
        .where(UserCredential.is_active.is_(True))
        .limit(1)
    ).first()
    if not credential:
        return None

    token = _issue_password_reset_token(db, str(person.id), email)
    return {
        "token": token,
        "email": email,
        "person_name": person.display_name or person.first_name,
    }


def reset_password(db: Session, token: str, new_password: str) -> datetime:
    """
    Reset password using a valid reset token.
    Returns the timestamp when password was reset.
    """
    payload = _decode_password_reset_token(db, token)
    person_id = payload.get("sub")
    email = payload.get("email")

    if not person_id or not email:
        raise HTTPException(status_code=401, detail="Invalid reset token")

    person = db.get(Person, coerce_uuid(person_id))
    if not person or person.email != email:
        raise HTTPException(status_code=401, detail="Invalid reset token")

    credential = db.scalars(
        select(UserCredential)
        .where(UserCredential.person_id == person.id)
        .where(UserCredential.is_active.is_(True))
        .limit(1)
    ).first()
    if not credential:
        raise HTTPException(status_code=404, detail="No credentials found")

    now = _now()
    credential.password_hash = hash_password(new_password)
    credential.password_updated_at = now
    credential.must_change_password = False
    credential.failed_login_attempts = 0
    credential.locked_until = None
    revoke_sessions_for_person(db, str(person.id))
    db.flush()

    return now
