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
    dom = VoiceDomain(customer_id="c1", fusionpbx_domain="c1.local")
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
    status = reconcile_voice(db_session, client, "c1")

    # Verify results
    assert "1002" in client.created
    assert status == SyncStatus.synced
    assert dom.sync_status == SyncStatus.synced
