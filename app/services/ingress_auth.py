import base64
import binascii
import secrets

from fastapi import Header, HTTPException, Request, status

from app.config import settings
from app.middleware.rate_limit import _get_client_ip


def _csv(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def _allowed_api_key(candidate: str, allowed_keys: tuple[str, ...]) -> bool:
    return any(secrets.compare_digest(candidate, allowed) for allowed in allowed_keys)


def _key_from_basic_auth(authorization: str | None) -> str | None:
    """Extract the ingress key from a Basic auth header (key = the password).

    Clients that can only do HTTP Basic auth (e.g. FreeSWITCH ``mod_json_cdr``'s
    ``cred`` param, which can't set a custom ``X-API-Key`` header) authenticate
    this way: the username is ignored and the password is treated as the key.
    """
    if not authorization or not authorization.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:].strip()).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return None
    if ":" not in decoded:
        return None
    _user, _, password = decoded.partition(":")
    return password or None


def require_ingress(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    allowed_keys = _csv(settings.voice_ingress_api_keys)
    candidate = x_api_key or _key_from_basic_auth(authorization)
    if not candidate or not _allowed_api_key(candidate, allowed_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key"
        )
    allowed_ips = _csv(settings.voice_ingress_allowed_ips)
    if allowed_ips:
        client_ip = _get_client_ip(request)
        if client_ip not in allowed_ips:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="ip not allowed"
            )
