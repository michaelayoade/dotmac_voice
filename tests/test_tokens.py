from jose import jwt
from app.services.tokens import mint_token
from app.config import settings

INGRESS = {"X-API-Key": "test-ingress-key"}


def test_mint_token_encodes_scope_and_exp():
    out = mint_token("subscriber-1", "queue:support", 60)
    claims = jwt.decode(out["token"], settings.token_signing_key, algorithms=["HS256"])
    assert claims["sub"] == "subscriber-1" and claims["scope"] == "queue:support"
    assert out["wss_endpoint"] == settings.edge_wss_url and out["expires_in"] == 60


def test_tokens_endpoint_requires_key(client):
    assert client.post("/tokens", json={"subject": "s1", "scope": "queue:support"}).status_code == 401


def test_tokens_endpoint_mints(client):
    r = client.post("/tokens", json={"subject": "s1", "scope": "queue:support"}, headers=INGRESS)
    assert r.status_code == 201 and r.json()["scope"] == "queue:support"


def test_tokens_endpoint_rejects_zero_ttl(client):
    r = client.post("/tokens", json={"subject": "s1", "scope": "queue:support", "ttl_seconds": 0}, headers=INGRESS)
    assert r.status_code == 422


def test_tokens_endpoint_rejects_excessive_ttl(client):
    r = client.post("/tokens", json={"subject": "s1", "scope": "queue:support", "ttl_seconds": 99999}, headers=INGRESS)
    assert r.status_code == 422


def test_mint_token_clamps_excessive_ttl():
    """Service-level defense: ttl clamped to max 300 even when called directly."""
    out = mint_token("s1", "queue:support", 99999)
    assert out["expires_in"] == 300
    # Verify the exp claim is also clamped (approximately now + 300)
    from datetime import datetime, UTC
    claims = jwt.decode(out["token"], settings.token_signing_key, algorithms=["HS256"])
    now = int(datetime.now(UTC).timestamp())
    assert 298 <= claims["exp"] - now <= 302  # Allow 2s tolerance for execution time


def test_mint_token_clamps_zero_ttl():
    """Service-level defense: ttl clamped to min 1 even when called directly."""
    out = mint_token("s1", "queue:support", 0)
    assert out["expires_in"] == 1
    # Verify the exp claim is also clamped (approximately now + 1)
    from datetime import datetime, UTC
    claims = jwt.decode(out["token"], settings.token_signing_key, algorithms=["HS256"])
    now = int(datetime.now(UTC).timestamp())
    assert 0 <= claims["exp"] - now <= 2  # Allow 1s tolerance for execution time
