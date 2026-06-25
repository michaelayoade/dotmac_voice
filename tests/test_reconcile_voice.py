"""Tests for voice reconciliation service."""

from app.models.voice import Extension, SyncStatus, VoiceDomain
from app.services.reconcile.voice import compute_delta, reconcile_voice


def test_compute_delta_diffs_sets():
    """Test that compute_delta correctly identifies extensions to create/delete."""
    d = compute_delta({"1001", "1002"}, {"1001"})
    assert d.to_create == {"1002"} and d.to_delete == set()


class _FakeClient:
    """Mock FusionPBX client for testing."""
    def __init__(self):
        self.created = []
        self.voicemails = []
        self.deleted_voicemails = []
        self.routed = []

    def ensure_switch_settings(self):
        return {"changed": False}

    def ensure_routing(self, domain, *, recording=False):
        self.routed.append(domain)
        return {"name": "kamailio-internal-to-domain", "created": True}

    def list_managed_dialplans(self, domain):
        return set()

    def list_queues(self, domain):
        return set()

    def list_extensions(self, domain):
        """Mock list_extensions: currently only knows about 1001."""
        return [{"number": "1001"}]

    def create_extension(self, domain, number, password, display_name=""):
        """Mock create_extension: record that this number was created."""
        self.created.append(number)

    def ensure_voicemail(self, domain, number, *, enabled=True, password=""):
        """Mock ensure_voicemail: record the voicemail box ensured."""
        self.voicemails.append(number)
        return {"voicemail_id": number, "created": True}

    def delete_voicemail(self, domain, number):
        self.deleted_voicemails.append(number)
        return True

    def delete_extension(self, domain, number):
        return True

    def delete_dialplan(self, name):
        return True

    def delete_queue(self, domain, number):
        return True


def test_reconcile_creates_missing_extension(db_session):
    """Test that reconcile_voice creates missing extensions and sets sync_status."""
    # Create domain
    dom = VoiceDomain(customer_id="recon-c1", fusionpbx_domain="recon-c1.local")
    db_session.add(dom)
    db_session.flush()

    # Create extensions
    db_session.add_all([
        Extension(voice_domain_id=dom.id, number="1001"),
        Extension(voice_domain_id=dom.id, number="1002"),
    ])
    db_session.flush()

    # Run reconciliation
    client = _FakeClient()
    status = reconcile_voice(db_session, client, "recon-c1")

    # Verify results
    assert "1002" in client.created
    assert status == SyncStatus.synced
    assert dom.sync_status == SyncStatus.synced


class _FakeClientWithExtras:
    """Mock FusionPBX client with extra extensions not in desired state."""
    def __init__(self):
        self.created = []
        self.deleted = []
        self.deleted_voicemails = []

    def list_extensions(self, domain):
        """Mock list_extensions: returns desired + extra 9999."""
        return [{"number": "1001"}, {"number": "9999"}]

    def create_extension(self, domain, number, password, display_name=""):
        """Mock create_extension: record that this number was created."""
        self.created.append(number)

    def delete_extension(self, domain, number):
        """Mock delete_extension: record that this number was deleted."""
        self.deleted.append(number)
        return True

    def delete_voicemail(self, domain, number):
        self.deleted_voicemails.append(number)
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


def test_reconcile_deletes_extra_extensions(db_session):
    """Test that reconcile_voice removes live FusionPBX extensions absent from desired state."""
    # Create domain
    dom = VoiceDomain(customer_id="drift-c1", fusionpbx_domain="drift-c1.local")
    db_session.add(dom)
    db_session.flush()

    # Create only desired extension (1001)
    db_session.add(Extension(voice_domain_id=dom.id, number="1001"))
    db_session.flush()

    # Run reconciliation with client that has extra 9999
    client = _FakeClientWithExtras()
    status = reconcile_voice(db_session, client, "drift-c1")

    # Verify exact desired state enforcement
    assert status == SyncStatus.synced
    assert dom.sync_status == SyncStatus.synced
    assert client.deleted == ["9999"]
    assert client.deleted_voicemails == ["9999"]

    # Reconcile idempotently re-applies desired extensions (upsert for metadata),
    # so 1001 is re-sent to the client; only the drift extension 9999 is deleted.
    assert client.created == ["1001"]


def test_reconcile_ensures_voicemail_for_enabled_extensions(db_session):
    """reconcile_voice provisions a voicemail box for each voicemail-enabled extension."""
    dom = VoiceDomain(customer_id="vm-c1", fusionpbx_domain="vm-c1.local")
    db_session.add(dom)
    db_session.flush()
    db_session.add_all([
        Extension(voice_domain_id=dom.id, number="1001", voicemail_enabled=True),
        Extension(voice_domain_id=dom.id, number="1002", voicemail_enabled=False),
    ])
    db_session.flush()

    client = _FakeClient()
    reconcile_voice(db_session, client, "vm-c1")

    # 1001 has voicemail enabled -> box ensured; 1002 disabled -> not.
    assert "1001" in client.voicemails
    assert "1002" not in client.voicemails
    assert "1002" in client.deleted_voicemails


def test_reconcile_passes_display_name_to_extension_upsert(db_session):
    """Existing/missing extension upserts include caller-ID display names."""
    dom = VoiceDomain(customer_id="name-c1", fusionpbx_domain="name-c1.local")
    db_session.add(dom)
    db_session.flush()
    db_session.add(Extension(voice_domain_id=dom.id, number="1001", display_name="Alice"))
    db_session.flush()

    class _Fake(_FakeClient):
        def __init__(self):
            super().__init__()
            self.upserts = []

        def list_extensions(self, domain):
            return [{"number": "1001"}]

        def create_extension(self, domain, number, password, display_name=""):
            self.upserts.append((number, display_name))

    client = _Fake()
    reconcile_voice(db_session, client, "name-c1")

    assert ("1001", "Alice") in client.upserts


def test_reconcile_suspend_removes_extensions_keeps_models(db_session):
    """Suspended (is_active=False): reconcile removes the customer's FusionPBX
    extensions, but the dotmac_voice Extension models are preserved for resume."""
    from sqlalchemy import select as _select

    dom = VoiceDomain(
        customer_id="susprec-c1", fusionpbx_domain="susprec-c1.local", is_active=False
    )
    db_session.add(dom)
    db_session.flush()
    db_session.add(Extension(voice_domain_id=dom.id, number="1001"))
    db_session.flush()

    class _Fake:
        def __init__(self):
            self.deleted = []

        def list_extensions(self, d):
            return [{"number": "1001"}]  # present on the PBX

        def create_extension(self, d, n, password, display_name=""):
            raise AssertionError("must not create while suspended")

        def delete_extension(self, d, n):
            self.deleted.append(n)
            return True

        def delete_voicemail(self, d, n):
            return True

        def ensure_voicemail(self, d, n, *, enabled=True, password=""):
            raise AssertionError("must not ensure voicemail while suspended")

        def ensure_switch_settings(self):
            raise AssertionError("must not bootstrap while suspended")

        def ensure_routing(self, d, *, recording=False):
            raise AssertionError("must not ensure routing while suspended")

        def list_managed_dialplans(self, d):
            return set()

        def list_queues(self, d):
            return set()

        def delete_dialplan(self, name):
            return True

        def delete_queue(self, d, n):
            return True

    fake = _Fake()
    reconcile_voice(db_session, fake, "susprec-c1")
    assert fake.deleted == ["1001"]  # removed from PBX
    exts = list(
        db_session.scalars(_select(Extension).where(Extension.voice_domain_id == dom.id))
    )
    assert len(exts) == 1  # model preserved


def test_reconcile_suspend_removes_live_features(db_session):
    """Suspension removes PBX-hosted feature entry points as well as extensions."""
    dom = VoiceDomain(
        customer_id="suspf-c1", fusionpbx_domain="suspf-c1.local", is_active=False
    )
    db_session.add(dom)
    db_session.flush()
    db_session.add(Extension(voice_domain_id=dom.id, number="1001"))
    db_session.flush()

    class _Fake:
        def __init__(self):
            self.deleted_extensions = []
            self.deleted_dialplans = []
            self.deleted_queues = []

        def list_extensions(self, d):
            return [{"number": "1001"}]

        def create_extension(self, d, n, password, display_name=""):
            raise AssertionError("must not create while suspended")

        def delete_extension(self, d, n):
            self.deleted_extensions.append(n)
            return True

        def delete_voicemail(self, d, n):
            return True

        def ensure_voicemail(self, d, n, *, enabled=True, password=""):
            raise AssertionError("must not ensure voicemail while suspended")

        def ensure_switch_settings(self):
            raise AssertionError("must not bootstrap while suspended")

        def ensure_routing(self, d, *, recording=False):
            raise AssertionError("must not ensure routing while suspended")

        def list_managed_dialplans(self, d):
            return {"kamailio-ivr-suspf-c1.local-4000"}

        def list_queues(self, d):
            return {"5000"}

        def delete_dialplan(self, name):
            self.deleted_dialplans.append(name)
            return True

        def delete_queue(self, d, n):
            self.deleted_queues.append(n)
            return True

    fake = _Fake()
    reconcile_voice(db_session, fake, "suspf-c1")

    assert fake.deleted_extensions == ["1001"]
    assert fake.deleted_dialplans == ["kamailio-ivr-suspf-c1.local-4000"]
    assert fake.deleted_queues == ["5000"]


def test_reconcile_applies_and_drifts_features(db_session):
    """reconcile applies feature desired-state models and deletes undefined (drift)."""
    from app.models.voice import ConferenceRoom, IvrMenu, Queue, RingGroup

    dom = VoiceDomain(customer_id="featrec-c1", fusionpbx_domain="featrec-c1.local")
    db_session.add(dom)
    db_session.flush()
    db_session.add_all([
        ConferenceRoom(voice_domain_id=dom.id, number="3001"),
        RingGroup(voice_domain_id=dom.id, number="2000", members=["1002", "1003"]),
        IvrMenu(voice_domain_id=dom.id, number="4000", options={"1": "1002"}),
        Queue(voice_domain_id=dom.id, number="5000", agents=["1002"], name="Support"),
    ])
    db_session.flush()

    class _Fake:
        def __init__(self):
            self.created = []
            self.deleted_dialplans = []
            self.deleted_queues = []
            self.queue_calls = []

        def list_extensions(self, d):
            return []

        def create_extension(self, d, n, password, display_name=""):
            pass

        def delete_extension(self, d, n):
            return True

        def delete_voicemail(self, d, n):
            return True

        def ensure_voicemail(self, d, n, *, enabled=True, password=""):
            return {}

        def ensure_switch_settings(self):
            return {}

        def ensure_routing(self, d, *, recording=False):
            return {}

        def create_conference(self, d, number):
            self.created.append(("conf", number))

        def create_ring_group(self, d, number, members, *, strategy="simultaneous", timeout=30):
            self.created.append(("rg", number, tuple(members)))

        def create_ivr(self, d, number, options, *, greeting="x"):
            self.created.append(("ivr", number))

        def ensure_queue(self, d, number, *, agents, name=None, strategy="ring-all"):
            self.queue_calls.append(number)
            return {}

        def list_managed_dialplans(self, d):
            # domain-scoped names; includes an orphan (9999) not in the desired models
            return {
                "kamailio-conference-featrec-c1.local-3001",
                "kamailio-ringgroup-featrec-c1.local-2000",
                "kamailio-ivr-featrec-c1.local-4000",
                "kamailio-ivr-featrec-c1.local-9999",
            }

        def list_queues(self, d):
            return {"5000", "5999"}  # 5999 is an orphan

        def delete_dialplan(self, name):
            self.deleted_dialplans.append(name)
            return True

        def delete_queue(self, d, number):
            self.deleted_queues.append(number)
            return True

    fake = _Fake()
    reconcile_voice(db_session, fake, "featrec-c1")

    assert {"conf", "rg", "ivr"} <= {c[0] for c in fake.created}
    assert "5000" in fake.queue_calls
    # drift: undefined feature dialplan + queue removed; defined ones kept
    assert fake.deleted_dialplans == ["kamailio-ivr-featrec-c1.local-9999"]
    assert fake.deleted_queues == ["5999"]


def test_reconcile_ensures_internal_routing(db_session):
    """reconcile_voice ensures the FS-in-path internal routing dialplan for the domain."""
    dom = VoiceDomain(customer_id="rt-c1", fusionpbx_domain="rt-c1.local")
    db_session.add(dom)
    db_session.flush()
    db_session.add(Extension(voice_domain_id=dom.id, number="1001"))
    db_session.flush()

    client = _FakeClient()
    reconcile_voice(db_session, client, "rt-c1")

    assert "rt-c1.local" in client.routed
