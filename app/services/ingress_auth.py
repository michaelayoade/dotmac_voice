import secrets

from fastapi import Header, HTTPException, Request, status

from app.config import settings
from app.middleware.rate_limit import _get_client_ip


def _csv(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def _allowed_api_key(candidate: str, allowed_keys: tuple[str, ...]) -> bool:
    return any(secrets.compare_digest(candidate, allowed) for allowed in allowed_keys)


def require_ingress(
    request: Request, x_api_key: str | None = Header(default=None)
) -> None:
    allowed_keys = _csv(settings.voice_ingress_api_keys)
    if not x_api_key or not _allowed_api_key(x_api_key, allowed_keys):
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
