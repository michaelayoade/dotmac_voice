"""Tests for provisioning-intent API endpoint."""
INGRESS = {"X-API-Key": "test-ingress-key"}


class _FakeClient:
    def list_extensions(self, domain): return []
    def create_extension(self, domain, number, password, display_name=""): pass


def test_put_provisioning_creates_and_syncs(client):
    from app.api.provisioning import get_fusionpbx_client
    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _FakeClient()
    body = {"fusionpbx_domain": "c1.local", "extensions": [{"number": "1001"}, {"number": "1002"}]}
    r = client.put("/provisioning/domains/c1", json=body, headers=INGRESS)
    assert r.status_code == 200
    assert r.json()["sync_status"] == "synced"
    client.app.dependency_overrides.pop(get_fusionpbx_client)


def test_put_provisioning_requires_key(client):
    r = client.put("/provisioning/domains/c1", json={"fusionpbx_domain": "c1.local", "extensions": []})
    assert r.status_code == 401
