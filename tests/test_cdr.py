"""Tests for CDR ingest and feed endpoints."""
import uuid

INGRESS = {"X-API-Key": "test-ingress-key"}


def _sample_payload(call_uuid: str) -> dict:
    return {
        "variables": {
            "uuid": call_uuid,
            "direction": "inbound",
            "caller_id_number": "2348012345678",
            "destination_number": "support",
            "duration": "42",
            "billsec": "30",
            "hangup_cause": "NORMAL_CLEARING",
            "start_epoch": "1750000000",
            "answer_epoch": "1750000005",
            "end_epoch": "1750000047",
            "variable_dotmac_subscriber_id": "sub-1",
        }
    }


def test_ingest_parses_json_cdr(client, db_session):
    """POST a mod_json_cdr payload; verify 201 and correct field mapping."""
    call_uuid = str(uuid.uuid4())
    r = client.post("/cdr/ingest", json=_sample_payload(call_uuid), headers=INGRESS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["call_uuid"] == call_uuid
    assert body["rating_status"] == "raw"

    # Verify DB fields
    from sqlalchemy import select
    from app.models.voice import Cdr, CdrRatingStatus

    cdr = db_session.scalar(select(Cdr).where(Cdr.call_uuid == call_uuid))
    assert cdr is not None
    assert cdr.duration_seconds == 42
    assert cdr.billsec == 30
    assert cdr.customer_id == "sub-1"
    assert cdr.callee == "support"
    assert cdr.rating_status == CdrRatingStatus.raw


def test_feed_returns_raw_cdrs(client):
    """After ingest, GET /cdr?rating_status=raw returns the row."""
    call_uuid = str(uuid.uuid4())
    post_r = client.post("/cdr/ingest", json=_sample_payload(call_uuid), headers=INGRESS)
    assert post_r.status_code == 201, post_r.text

    get_r = client.get("/cdr?rating_status=raw", headers=INGRESS)
    assert get_r.status_code == 200, get_r.text
    items = get_r.json()
    assert isinstance(items, list)
    uuids = [item["call_uuid"] for item in items]
    assert call_uuid in uuids


def test_cdr_requires_ingress_key(client):
    """POST and GET without the API key must return 401."""
    call_uuid = str(uuid.uuid4())
    post_r = client.post("/cdr/ingest", json=_sample_payload(call_uuid))
    assert post_r.status_code == 401

    get_r = client.get("/cdr?rating_status=raw")
    assert get_r.status_code == 401
