"""Tests for voice reconciliation service."""
from app.services.reconcile.voice import compute_delta, reconcile_voice
from app.models.voice import VoiceDomain, Extension, SyncStatus


def test_compute_delta_diffs_sets():
    """Test that compute_delta correctly identifies extensions to create/delete."""
    d = compute_delta({"1001", "1002"}, {"1001"})
    assert d.to_create == {"1002"} and d.to_delete == set()


class _FakeClient:
    """Mock FusionPBX client for testing."""
    def __init__(self):
        self.created = []

    def list_extensions(self, domain):
        """Mock list_extensions: currently only knows about 1001."""
        return [{"number": "1001"}]

    def create_extension(self, domain, number, password, display_name=""):
        """Mock create_extension: record that this number was created."""
        self.created.append(number)


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

    def list_extensions(self, domain):
        """Mock list_extensions: returns desired + extra 9999."""
        return [{"number": "1001"}, {"number": "9999"}]

    def create_extension(self, domain, number, password, display_name=""):
        """Mock create_extension: record that this number was created."""
        self.created.append(number)


def test_reconcile_marks_drift_on_extras(db_session):
    """Test that reconcile_voice marks drift when FusionPBX has extra extensions.

    Tier-0 policy: drift, never delete. If desired state is {1001} but FusionPBX
    has {1001, 9999}, mark as drift and do NOT attempt deletion.
    """
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

    # Verify drift status returned and set on domain
    assert status == SyncStatus.drift
    assert dom.sync_status == SyncStatus.drift

    # Verify no deletions attempted (client has no delete method)
    assert not hasattr(client, 'deleted'), "Client should have no delete_extension method"

    # Verify no creations (1001 already exists)
    assert len(client.created) == 0
