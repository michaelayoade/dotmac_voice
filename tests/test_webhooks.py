"""Tests for outbound webhook delivery to CRM."""
import hashlib
import hmac
import uuid

import pytest
import respx
import httpx

from tests.conftest import _TestSessionLocal


INGRESS_HEADERS = {"X-API-Key": "test-ingress-key"}


# ─────────────────────────────────────────────────────────────────────────────
# Signing helper
# ─────────────────────────────────────────────────────────────────────────────


def test_sign_body_known_vector():
    """sign_body returns sha256=<hex HMAC-SHA256(secret, body)>."""
    from app.services.webhooks.delivery import sign_body

    secret = "s3cret"
    body = b'{"a":1}'
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign_body(secret, body) == expected


# ─────────────────────────────────────────────────────────────────────────────
# attempt_delivery – success path
# ─────────────────────────────────────────────────────────────────────────────


def test_attempt_delivery_success():
    """attempt_delivery returns True, marks delivered, and sends correct signature."""
    from app.models.webhook import WebhookEndpoint, WebhookDelivery, DeliveryStatus
    from app.services.webhooks.delivery import attempt_delivery, sign_body

    db = _TestSessionLocal()
    try:
        endpoint = WebhookEndpoint(
            url="https://crm.example.com/webhooks/voice",
            secret="mysecret",
            event_types=["call.ended"],
            active=True,
        )
        db.add(endpoint)
        db.flush()

        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event_type="call.ended",
            payload={"call_uuid": "abc123", "duration": 60},
            status=DeliveryStatus.pending,
        )
        db.add(delivery)
        db.flush()

        import json

        body_bytes = json.dumps(delivery.payload).encode()
        expected_sig = sign_body(endpoint.secret, body_bytes)

        with respx.mock:
            route = respx.post("https://crm.example.com/webhooks/voice").mock(
                return_value=httpx.Response(200)
            )
            result = attempt_delivery(db, delivery)

        assert result is True
        assert delivery.status == DeliveryStatus.delivered
        assert delivery.attempts == 1

        # Verify correct signature was sent
        sent_request = route.calls[0].request
        assert sent_request.headers["X-Webhook-Signature-256"] == expected_sig
        assert sent_request.headers["X-Webhook-Event"] == "call.ended"
        assert sent_request.headers["X-Webhook-Delivery-Id"] == str(delivery.id)
    finally:
        db.rollback()
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# attempt_delivery – failure → dead-letter
# ─────────────────────────────────────────────────────────────────────────────


def test_attempt_delivery_failure_then_deadletter():
    """After max_attempts failures, status becomes failed with last_status_code."""
    from app.models.webhook import WebhookEndpoint, WebhookDelivery, DeliveryStatus
    from app.services.webhooks.delivery import attempt_delivery

    db = _TestSessionLocal()
    try:
        endpoint = WebhookEndpoint(
            url="https://crm.example.com/webhooks/down",
            secret="anothersecret",
            event_types=["call.started"],
            active=True,
        )
        db.add(endpoint)
        db.flush()

        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event_type="call.started",
            payload={"call_uuid": "xyz"},
            status=DeliveryStatus.pending,
        )
        db.add(delivery)
        db.flush()

        with respx.mock:
            respx.post("https://crm.example.com/webhooks/down").mock(
                return_value=httpx.Response(500)
            )

            # Call 5 times (max_attempts=5)
            for _ in range(5):
                result = attempt_delivery(db, delivery, max_attempts=5)

        assert result is False
        assert delivery.status == DeliveryStatus.failed
        assert delivery.attempts == 5
        assert delivery.last_status_code == 500
    finally:
        db.rollback()
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# endpoints_for_event
# ─────────────────────────────────────────────────────────────────────────────


def test_endpoints_for_event_filters():
    """endpoints_for_event returns only endpoints subscribed to the given event."""
    from app.models.webhook import WebhookEndpoint
    from app.services.webhooks.delivery import endpoints_for_event

    db = _TestSessionLocal()
    try:
        ep_a = WebhookEndpoint(
            url="https://a.example.com/hook",
            secret="secA",
            event_types=["call.ended", "call.started"],
            active=True,
        )
        ep_b = WebhookEndpoint(
            url="https://b.example.com/hook",
            secret="secB",
            event_types=["call.started"],
            active=True,
        )
        db.add_all([ep_a, ep_b])
        db.commit()

        result = endpoints_for_event(db, "call.ended")

        ids = {ep.id for ep in result}
        assert ep_a.id in ids
        assert ep_b.id not in ids
    finally:
        db.rollback()
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# API – register and delete endpoint
# ─────────────────────────────────────────────────────────────────────────────


def test_register_and_delete_endpoint(client):
    """POST creates endpoint (201), GET lists it, DELETE removes it (204)."""
    payload = {
        "url": "https://crm.example.com/webhooks/voice",
        "secret": "supersecret",
        "event_types": ["call.ended"],
        "active": True,
    }

    # Create
    resp = client.post("/webhooks/endpoints", json=payload, headers=INGRESS_HEADERS)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    ep_id = created["id"]
    assert created["url"] == payload["url"]
    assert created["event_types"] == payload["event_types"]

    # List
    resp = client.get("/webhooks/endpoints", headers=INGRESS_HEADERS)
    assert resp.status_code == 200
    ids = [ep["id"] for ep in resp.json()]
    assert ep_id in ids

    # Delete
    resp = client.delete(f"/webhooks/endpoints/{ep_id}", headers=INGRESS_HEADERS)
    assert resp.status_code == 204

    # Confirm gone
    resp = client.get("/webhooks/endpoints", headers=INGRESS_HEADERS)
    assert resp.status_code == 200
    ids_after = [ep["id"] for ep in resp.json()]
    assert ep_id not in ids_after


# ─────────────────────────────────────────────────────────────────────────────
# API – auth guard
# ─────────────────────────────────────────────────────────────────────────────


def test_webhooks_requires_ingress(client):
    """Requests without X-API-Key are rejected with 401."""
    resp = client.get("/webhooks/endpoints")
    assert resp.status_code == 401

    resp = client.post(
        "/webhooks/endpoints",
        json={"url": "https://x.com", "secret": "s", "event_types": []},
    )
    assert resp.status_code == 401


def test_sweep_reattempts_only_stuck_pending():
    """sweep_deliveries re-attempts pending deliveries with attempts<max only —
    skipping delivered and exhausted (maxed-out) rows."""
    from unittest.mock import patch

    from app.models.webhook import DeliveryStatus, WebhookDelivery, WebhookEndpoint
    from app.services.webhooks import delivery as dmod

    db = _TestSessionLocal()
    try:
        ep = WebhookEndpoint(
            url="https://crm.example.com/wh", secret="s",
            event_types=["call.ended"], active=True,
        )
        db.add(ep)
        db.flush()
        pending = WebhookDelivery(
            endpoint_id=ep.id, event_type="call.ended", payload={},
            status=DeliveryStatus.pending, attempts=1,
        )
        delivered = WebhookDelivery(
            endpoint_id=ep.id, event_type="call.ended", payload={},
            status=DeliveryStatus.delivered, attempts=1,
        )
        maxed = WebhookDelivery(
            endpoint_id=ep.id, event_type="call.ended", payload={},
            status=DeliveryStatus.pending, attempts=5,
        )
        db.add_all([pending, delivered, maxed])
        db.flush()

        attempted = []
        with patch.object(
            dmod, "attempt_delivery",
            side_effect=lambda db, d, **kw: attempted.append(d.id) or True,
        ):
            n = dmod.sweep_deliveries(db, max_attempts=5)
        assert n == 1
        assert attempted == [pending.id]
    finally:
        db.rollback()
        db.close()
