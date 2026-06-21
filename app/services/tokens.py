from datetime import UTC, datetime, timedelta

from jose import jwt

from app.config import settings

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
