import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta

from jose import jwt

from app.config import settings


def turn_credentials(secret: str, ttl: int) -> tuple[str, str]:
    """coturn REST (static-auth-secret) ephemeral credentials:
    username = <expiry-epoch>, password = base64(HMAC-SHA1(secret, username))."""
    username = str(int(time.time()) + ttl)
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()


def build_ice_servers() -> list[dict]:
    """STUN + (when a TURN secret is configured) ephemeral-TURN ICE servers."""
    servers: list[dict] = []
    stun = [u.strip() for u in settings.stun_urls.split(",") if u.strip()]
    if stun:
        servers.append({"urls": stun})
    turn = [u.strip() for u in settings.turn_urls.split(",") if u.strip()]
    if settings.turn_static_auth_secret and turn:
        username, credential = turn_credentials(
            settings.turn_static_auth_secret, settings.turn_credential_ttl
        )
        servers.append({"urls": turn, "username": username, "credential": credential})
    return servers


_MIN_TTL_SECONDS = 1
_MAX_TTL_SECONDS = 300


def mint_token(subject: str, scope: str, ttl_seconds: int = 60) -> dict:
    """Create an ephemeral SIP/WebRTC token with JWT claims."""
    # Defense in depth: clamp ttl at service level
    ttl_seconds = max(_MIN_TTL_SECONDS, min(ttl_seconds, _MAX_TTL_SECONDS))

    now = datetime.now(UTC)
    exp_time = now + timedelta(seconds=ttl_seconds)
    claims = {
        "sub": subject,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int(exp_time.timestamp()),
    }
    token = jwt.encode(claims, settings.token_signing_key, algorithm="HS256")
    return {
        "token": token,
        "sip_uri": f"sip:{subject}@dotmac.io",
        "wss_endpoint": settings.edge_wss_url,
        "expires_in": ttl_seconds,
        "scope": scope,
    }
