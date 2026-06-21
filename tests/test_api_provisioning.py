"""Tests for provisioning-intent API endpoint."""
INGRESS = {"X-API-Key": "test-ingress-key"}


class _FakeClient:
    def list_extensions(self, domain): return []
    def create_extension(self, domain, number, password, display_name=""): pass


def test_put_provisioning_creates_and_syncs(client):
    from app.api.provisioning import get_fusionpbx_client
    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _FakeClient()
    body = {"fusionpbx_domain": "prov-c1.local", "extensions": [{"number": "1001"}, {"number": "1002"}]}
    r = client.put("/provisioning/domains/prov-c1", json=body, headers=INGRESS)
    assert r.status_code == 200
    assert r.json()["sync_status"] == "synced"
    client.app.dependency_overrides.pop(get_fusionpbx_client)


def test_put_provisioning_requires_key(client):
    r = client.put("/provisioning/domains/prov-c2", json={"fusionpbx_domain": "prov-c2.local", "extensions": []})
    assert r.status_code == 401


def test_put_provisioning_replaces_extension_set(client, db_session):
    """Test that PUT replaces the desired extension set entirely (add, remove, update)."""
    from app.api.provisioning import get_fusionpbx_client
    from app.models.voice import Extension, VoiceDomain
    from sqlalchemy import select

    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _FakeClient()

    # First PUT: create domain with extensions 1001, 1002
    body1 = {
        "fusionpbx_domain": "replace-c1.local",
        "extensions": [
            {"number": "1001", "display_name": "Alice"},
            {"number": "1002", "display_name": "Bob"},
        ]
    }
    r1 = client.put("/provisioning/domains/replace-c1", json=body1, headers=INGRESS)
    assert r1.status_code == 200

    # Verify domain and extensions created
    domain = db_session.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == "replace-c1"))
    assert domain is not None
    exts = list(db_session.scalars(select(Extension).where(Extension.voice_domain_id == domain.id)))
    assert len(exts) == 2
    ext_by_number = {e.number: e for e in exts}
    assert "1001" in ext_by_number
    assert "1002" in ext_by_number
    assert ext_by_number["1001"].display_name == "Alice"
    assert ext_by_number["1002"].display_name == "Bob"

    # Second PUT: replace with 1001 (updated display name), 1003 (new), remove 1002
    body2 = {
        "fusionpbx_domain": "replace-c1.local",
        "extensions": [
            {"number": "1001", "display_name": "Alice Updated"},
            {"number": "1003", "display_name": "Charlie"},
        ]
    }
    r2 = client.put("/provisioning/domains/replace-c1", json=body2, headers=INGRESS)
    assert r2.status_code == 200

    # Verify extensions replaced: 1002 removed, 1001 updated, 1003 added
    exts = list(db_session.scalars(select(Extension).where(Extension.voice_domain_id == domain.id)))
    assert len(exts) == 2, f"Expected 2 extensions, got {len(exts)}"
    ext_by_number = {e.number: e for e in exts}
    assert set(ext_by_number.keys()) == {"1001", "1003"}, f"Expected {{'1001', '1003'}}, got {set(ext_by_number.keys())}"
    assert ext_by_number["1001"].display_name == "Alice Updated"
    assert ext_by_number["1003"].display_name == "Charlie"

    client.app.dependency_overrides.pop(get_fusionpbx_client)
