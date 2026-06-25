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


def test_turn_credentials_hmac():
    import base64
    import hashlib
    import hmac

    from app.services.tokens import turn_credentials

    username, cred = turn_credentials("s3cr3t", 3600)
    expected = base64.b64encode(
        hmac.new(b"s3cr3t", username.encode(), hashlib.sha1).digest()
    ).decode()
    assert cred == expected and int(username) > 0


def test_build_ice_servers_includes_turn(monkeypatch):
    from app.services import tokens as ts

    monkeypatch.setattr(ts.settings, "stun_urls", "stun:stun.example:3478")
    monkeypatch.setattr(ts.settings, "turn_static_auth_secret", "s3cr3t")
    monkeypatch.setattr(ts.settings, "turn_urls", "turn:turn.example:3478")
    servers = ts.build_ice_servers()
    assert any("stun:stun.example:3478" in s["urls"] for s in servers)
    turn = [s for s in servers if "username" in s]
    assert turn and turn[0]["credential"]


def test_build_ice_servers_omits_turn_without_secret(monkeypatch):
    from app.services import tokens as ts

    monkeypatch.setattr(ts.settings, "turn_static_auth_secret", "")
    monkeypatch.setattr(ts.settings, "turn_urls", "turn:turn.example:3478")
    assert all("username" not in s for s in ts.build_ice_servers())


def test_client_bootstrap(client, db_session):
    from app.api.provisioning import get_fusionpbx_client
    from app.models.voice import ConferenceRoom, Extension, VoiceDomain

    dom = VoiceDomain(customer_id="boot-c1", fusionpbx_domain="boot-c1.local")
    db_session.add(dom)
    db_session.flush()
    db_session.add(Extension(voice_domain_id=dom.id, number="1001", voicemail_enabled=True))
    db_session.add(ConferenceRoom(voice_domain_id=dom.id, number="3001"))
    db_session.commit()

    class _Fake:
        def get_extension_secret(self, domain, number):
            return "sip-secret-xyz" if number == "1001" else None

    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _Fake()
    r = client.post(
        "/tokens/bootstrap",
        json={"customer_id": "boot-c1", "extension": "1001"},
        headers=INGRESS,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sip"]["username"] == "1001"
    assert body["sip"]["domain"] == "boot-c1.local"
    assert body["sip"]["password"] == "sip-secret-xyz"
    assert body["entitlements"]["voicemail"] is True
    assert body["entitlements"]["conferences"] == ["3001"]
    assert body["token"]
    client.app.dependency_overrides.pop(get_fusionpbx_client)


def test_client_bootstrap_unknown_extension_404(client, db_session):
    from app.api.provisioning import get_fusionpbx_client
    from app.models.voice import VoiceDomain

    dom = VoiceDomain(customer_id="boot-c2", fusionpbx_domain="boot-c2.local")
    db_session.add(dom)
    db_session.commit()

    class _Fake:
        def get_extension_secret(self, domain, number):
            return None

    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _Fake()
    r = client.post(
        "/tokens/bootstrap",
        json={"customer_id": "boot-c2", "extension": "9999"},
        headers=INGRESS,
    )
    assert r.status_code == 404
    client.app.dependency_overrides.pop(get_fusionpbx_client)
