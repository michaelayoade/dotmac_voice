import uuid
from app.models.voice import VoiceDomain, Extension, SyncStatus


def test_create_voice_domain_with_extension(db_session):
    d = VoiceDomain(customer_id="cust-1", fusionpbx_domain="cust1.voice.local")
    db_session.add(d)
    db_session.flush()
    assert d.sync_status == SyncStatus.pending
    ext = Extension(voice_domain_id=d.id, number="1001", display_name="Front desk")
    db_session.add(ext)
    db_session.flush()
    assert ext.sync_status == SyncStatus.pending
    assert ext.voicemail_enabled is True
