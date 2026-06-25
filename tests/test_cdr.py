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
    post_r = client.post(
        "/cdr/ingest", json=_sample_payload(call_uuid), headers=INGRESS
    )
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


def test_ingest_tolerates_empty_numeric_vars(client, db_session):
    """POST a CDR with empty string and null numeric vars; verify 201 and stored zeros."""
    call_uuid = str(uuid.uuid4())
    payload = {
        "variables": {
            "uuid": call_uuid,
            "direction": "inbound",
            "caller_id_number": "2348012345678",
            "destination_number": "support",
            "duration": "",
            "billsec": None,
            "hangup_cause": "NORMAL_CLEARING",
            "start_epoch": "1750000000",
            "answer_epoch": "1750000005",
            "end_epoch": "1750000047",
            "variable_dotmac_subscriber_id": "sub-1",
        }
    }
    r = client.post("/cdr/ingest", json=payload, headers=INGRESS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["call_uuid"] == call_uuid

    # Verify DB row has zeros for empty/null numeric vars
    from sqlalchemy import select

    from app.models.voice import Cdr

    cdr = db_session.scalar(select(Cdr).where(Cdr.call_uuid == call_uuid))
    assert cdr is not None
    assert cdr.duration_seconds == 0
    assert cdr.billsec == 0


def test_feed_rejects_bad_limit(client):
    """GET /cdr with invalid limit values must return 422."""
    # limit=0 should fail (ge=1)
    r = client.get("/cdr?limit=0", headers=INGRESS)
    assert r.status_code == 422, r.text

    # limit=5000 should fail (le=1000)
    r = client.get("/cdr?limit=5000", headers=INGRESS)
    assert r.status_code == 422, r.text


def test_get_cdrs_by_customer(client, db_session):
    """GET /cdr?customer_id=X returns that customer's call history, newest first."""
    from app.models.voice import Cdr

    db_session.add(
        Cdr(
            call_uuid="cdrcust-u1",
            customer_id="cdr-cust1",
            caller="1001",
            callee="1002",
        )
    )
    db_session.add(
        Cdr(call_uuid="cdrcust-u2", customer_id="cdr-other", caller="x", callee="y")
    )
    db_session.commit()

    r = client.get("/cdr?customer_id=cdr-cust1", headers=INGRESS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert all(d["customer_id"] == "cdr-cust1" for d in data)
    assert "cdrcust-u1" in [d["call_uuid"] for d in data]

    # unknown customer -> empty list
    r2 = client.get("/cdr?customer_id=nobody-here", headers=INGRESS)
    assert r2.status_code == 200 and r2.json() == []


def test_ingest_is_idempotent_by_call_uuid(client, db_session):
    from sqlalchemy import func, select

    from app.models.voice import Cdr

    uid = "idem-" + uuid.uuid4().hex
    p = _sample_payload(uid)
    client.post("/cdr/ingest", json=p, headers=INGRESS)
    p["variables"]["billsec"] = "99"  # same call, corrected/retried payload
    client.post("/cdr/ingest", json=p, headers=INGRESS)

    n = db_session.scalar(
        select(func.count()).select_from(Cdr).where(Cdr.call_uuid == uid)
    )
    assert n == 1  # upsert, not duplicate
    cdr = db_session.scalar(select(Cdr).where(Cdr.call_uuid == uid))
    assert cdr.billsec == 99


def test_ingest_populates_recording_url(client, db_session):
    from sqlalchemy import select

    from app.models.voice import Cdr

    uid = "rec-" + uuid.uuid4().hex
    p = _sample_payload(uid)
    p["variables"]["variable_recording_file"] = (
        f"/var/lib/freeswitch/recordings/x/2026-06-25/{uid}.wav"
    )
    client.post("/cdr/ingest", json=p, headers=INGRESS)
    cdr = db_session.scalar(select(Cdr).where(Cdr.call_uuid == uid))
    assert cdr.recording_url and cdr.recording_url.endswith(f"{uid}.wav")


def test_mark_cdrs_transitions_rating(client, db_session):
    from sqlalchemy import select

    from app.models.voice import Cdr, CdrRatingStatus

    uid = "mark-" + uuid.uuid4().hex
    client.post("/cdr/ingest", json=_sample_payload(uid), headers=INGRESS)
    r = client.post(
        "/cdr/mark",
        json={"call_uuids": [uid], "rating_status": "rated"},
        headers=INGRESS,
    )
    assert r.status_code == 200 and r.json()["marked"] == 1
    db_session.expire_all()
    cdr = db_session.scalar(select(Cdr).where(Cdr.call_uuid == uid))
    assert cdr.rating_status == CdrRatingStatus.rated

    r2 = client.post(
        "/cdr/mark",
        json={"call_uuids": [uid], "rating_status": "bogus"},
        headers=INGRESS,
    )
    assert r2.status_code == 400
