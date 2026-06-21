from fastapi import Header, HTTPException, Request, status
from app.config import settings


def _csv(value: str) -> set[str]:
    return {v.strip() for v in value.split(",") if v.strip()}


def require_ingress(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    allowed_keys = _csv(settings.voice_ingress_api_keys)
    if not x_api_key or x_api_key not in allowed_keys:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    allowed_ips = _csv(settings.voice_ingress_allowed_ips)
    if allowed_ips:
        client_ip = request.client.host if request.client else ""
        if client_ip not in allowed_ips:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ip not allowed")
