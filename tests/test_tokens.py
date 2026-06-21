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
