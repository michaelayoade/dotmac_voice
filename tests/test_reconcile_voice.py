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
        self.routed = []

    def ensure_switch_settings(self):
        return {"changed": False}

    def ensure_routing(self, domain, *, recording=False):
        self.routed.append(domain)
        return {"name": "kamailio-internal-to-domain", "created": True}

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

    def ensure_voicemail(self, domain, number, *, enabled=True, password=""):
        return {"voicemail_id": number, "created": True}

    def ensure_switch_settings(self):
        return {"changed": False}

    def ensure_routing(self, domain, *, recording=False):
        return {"name": "kamailio-internal-to-domain", "created": True}


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

    # Verify no creations (1001 already exists)
    assert len(client.created) == 0


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
