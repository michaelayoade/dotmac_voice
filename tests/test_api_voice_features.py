"""Tests for per-feature voice provisioning endpoints."""

INGRESS = {"X-API-Key": "test-ingress-key"}


class _FakeClient:
    def __init__(self):
        self.calls = []

    # provisioning methods (used by the PUT that creates the domain)
    def list_extensions(self, domain):
        return []

    def create_extension(self, domain, number, password, display_name=""):
        pass

    def delete_extension(self, domain, number):
        return True

    def delete_voicemail(self, domain, number):
        return True

    def ensure_voicemail(self, domain, number, *, enabled=True, password=""):
        return {"voicemail_id": number, "created": True}

    def ensure_switch_settings(self):
        return {"changed": False}

    def ensure_routing(self, domain, *, recording=False):
        return {"name": "kamailio-internal-to-domain", "created": True}

    def list_managed_dialplans(self, domain):
        return set()

    def list_queues(self, domain):
        return set()

    def delete_dialplan(self, name):
        return True

    def delete_queue(self, domain, number):
        return True

    # feature primitives
    def create_conference(self, domain, number):
        self.calls.append(("conference", domain, number))
        return {"name": f"kamailio-conference-{number}", "created": True}

    def create_ring_group(self, domain, number, members, *, strategy="simultaneous", timeout=30):
        self.calls.append(("ringgroup", domain, number, tuple(members), strategy))
        return {"name": f"kamailio-ringgroup-{number}", "created": True}

    def create_ivr(self, domain, number, options, *, greeting="x", timeout=6000):
        self.calls.append(("ivr", domain, number, tuple(sorted(options))))
        return {"name": f"kamailio-ivr-{number}", "created": True}

    def ensure_queue(self, domain, number, *, agents, name=None, strategy="ring-all"):
        self.calls.append(("queue", domain, number, tuple(agents)))
        return {"name": f"kamailio-queue-{number}", "created": True}


def _provision_domain(client, fake, cid, dom):
    from app.api.provisioning import get_fusionpbx_client

    client.app.dependency_overrides[get_fusionpbx_client] = lambda: fake
    r = client.put(
        f"/provisioning/domains/{cid}",
        json={"fusionpbx_domain": dom, "extensions": []},
        headers=INGRESS,
    )
    assert r.status_code == 200


def test_feature_endpoints_call_primitives(client):
    from app.api.provisioning import get_fusionpbx_client

    fake = _FakeClient()
    _provision_domain(client, fake, "feat-c1", "feat-c1.local")

    base = "/provisioning/domains/feat-c1/features"
    assert client.post(f"{base}/conferences", json={"number": "3001"}, headers=INGRESS).status_code == 200
    assert client.post(
        f"{base}/ring-groups", json={"number": "2000", "members": ["1002", "1003"]}, headers=INGRESS
    ).status_code == 200
    assert client.post(
        f"{base}/ivrs", json={"number": "4000", "options": {"1": "1002", "2": "3001"}}, headers=INGRESS
    ).status_code == 200
    assert client.post(
        f"{base}/queues", json={"number": "5000", "agents": ["1002"]}, headers=INGRESS
    ).status_code == 200

    kinds = {c[0] for c in fake.calls}
    assert {"conference", "ringgroup", "ivr", "queue"} == kinds
    # domain resolved correctly for each
    assert all(c[1] == "feat-c1.local" for c in fake.calls)
    client.app.dependency_overrides.pop(get_fusionpbx_client)


def test_feature_delete_removes_model(client, db_session):
    from sqlalchemy import select

    from app.api.provisioning import get_fusionpbx_client
    from app.models.voice import ConferenceRoom, VoiceDomain

    fake = _FakeClient()
    _provision_domain(client, fake, "del-c1", "del-c1.local")
    base = "/provisioning/domains/del-c1/features"
    assert client.post(f"{base}/conferences", json={"number": "3001"}, headers=INGRESS).status_code == 200
    dom = db_session.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == "del-c1"))
    assert db_session.scalar(
        select(ConferenceRoom).where(ConferenceRoom.voice_domain_id == dom.id)
    ) is not None

    assert client.delete(f"{base}/conferences/3001", headers=INGRESS).status_code == 200
    db_session.expire_all()
    assert db_session.scalar(
        select(ConferenceRoom).where(ConferenceRoom.voice_domain_id == dom.id)
    ) is None
    client.app.dependency_overrides.pop(get_fusionpbx_client)


def test_feature_endpoint_requires_ingress(client):
    r = client.post("/provisioning/domains/x/features/conferences", json={"number": "3001"})
    assert r.status_code == 401


def test_feature_endpoint_rejects_bad_ivr_option(client):
    r = client.post(
        "/provisioning/domains/x/features/ivrs",
        json={"number": "4000", "options": {"12": "1002"}},
        headers=INGRESS,
    )
    assert r.status_code == 422


def test_feature_endpoint_unknown_domain_404(client):
    from app.api.provisioning import get_fusionpbx_client

    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _FakeClient()
    r = client.post(
        "/provisioning/domains/nope/features/conferences",
        json={"number": "3001"},
        headers=INGRESS,
    )
    assert r.status_code == 404
    client.app.dependency_overrides.pop(get_fusionpbx_client)


def test_put_features_replaces_full_set(client, db_session):
    from sqlalchemy import select

    from app.api.provisioning import get_fusionpbx_client
    from app.models.voice import ConferenceRoom, RingGroup, VoiceDomain

    fake = _FakeClient()
    _provision_domain(client, fake, "fs-c1", "fs-c1.local")
    base = "/provisioning/domains/fs-c1/features"

    r1 = client.put(
        base,
        json={
            "conferences": [{"number": "3001"}],
            "ring_groups": [{"number": "2000", "members": ["1002"]}],
        },
        headers=INGRESS,
    )
    assert r1.status_code == 200
    dom = db_session.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == "fs-c1"))
    confs = {c.number for c in db_session.scalars(
        select(ConferenceRoom).where(ConferenceRoom.voice_domain_id == dom.id))}
    assert confs == {"3001"}

    # Replace with a different set: 3001 removed, 3002 added, ring group removed.
    r2 = client.put(base, json={"conferences": [{"number": "3002"}]}, headers=INGRESS)
    assert r2.status_code == 200
    db_session.expire_all()
    confs = {c.number for c in db_session.scalars(
        select(ConferenceRoom).where(ConferenceRoom.voice_domain_id == dom.id))}
    assert confs == {"3002"}
    rgs = list(db_session.scalars(
        select(RingGroup).where(RingGroup.voice_domain_id == dom.id)))
    assert rgs == []
    client.app.dependency_overrides.pop(get_fusionpbx_client)
