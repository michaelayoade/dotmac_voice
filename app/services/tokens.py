from datetime import UTC, datetime, timedelta

from jose import jwt

from app.config import settings


def mint_token(subject: str, scope: str, ttl_seconds: int = 60) -> dict:
    """Create an ephemeral SIP/WebRTC token with JWT claims."""
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
