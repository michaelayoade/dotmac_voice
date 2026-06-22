"""Tests for ESL call event dispatch to CRM webhooks."""
import pytest

from app.models.webhook import DeliveryStatus, WebhookDelivery, WebhookEndpoint
from app.services.freeswitch.esl import CallEvent


# ---------------------------------------------------------------------------
# event_type_for mapping tests
# ---------------------------------------------------------------------------


def test_event_type_mapping():
    from app.services.events.dispatch import event_type_for

    assert event_type_for("CHANNEL_CREATE") == "call.ringing"
    assert event_type_for("CHANNEL_ANSWER") == "call.answered"
    assert event_type_for("CHANNEL_HANGUP_COMPLETE") == "call.ended"
    # CHANNEL_HANGUP is intentionally unmapped — we emit call.ended only from
    # CHANNEL_HANGUP_COMPLETE to avoid double-firing.
    assert event_type_for("CHANNEL_HANGUP") is None
    assert event_type_for("BOGUS") is None


# ---------------------------------------------------------------------------
# dispatch_call_event tests
# ---------------------------------------------------------------------------


def test_dispatch_creates_delivery_for_subscriber(db_session):
    from app.services.events.dispatch import dispatch_call_event

    ep = WebhookEndpoint(
        url="https://crm.example.com/webhook",
        secret="s3cr3t",
        event_types=["call.answered"],
        active=True,
    )
    db_session.add(ep)
    db_session.flush()

    event = CallEvent(
        call_uuid="u1",
        name="CHANNEL_ANSWER",
        direction="inbound",
        caller="2348012345678",
        callee="support",
        subscriber_id="sub-1",
    )

    deliveries = dispatch_call_event(db_session, event)

    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.status == DeliveryStatus.pending
    assert d.event_type == "call.answered"
    assert d.payload["call_uuid"] == "u1"
    assert d.payload["subscriber_id"] == "sub-1"


def test_dispatch_no_subscriber_no_delivery(db_session):
    from app.services.events.dispatch import dispatch_call_event

    ep = WebhookEndpoint(
        url="https://crm.example.com/webhook2",
        secret="s3cr3t",
        event_types=["call.ended"],
        active=True,
    )
    db_session.add(ep)
    db_session.flush()

    event = CallEvent(
        call_uuid="u2",
        name="CHANNEL_ANSWER",
        direction="inbound",
        caller="2348012345678",
        callee="support",
        subscriber_id="sub-2",
    )

    deliveries = dispatch_call_event(db_session, event)

    assert deliveries == []


def test_dispatch_unmapped_event_noop(db_session):
    from app.services.events.dispatch import dispatch_call_event

    # Even if an endpoint subscribes to something, CHANNEL_HANGUP is unmapped
    ep = WebhookEndpoint(
        url="https://crm.example.com/webhook3",
        secret="s3cr3t",
        event_types=["call.ringing", "call.answered", "call.ended"],
        active=True,
    )
    db_session.add(ep)
    db_session.flush()

    event = CallEvent(
        call_uuid="u3",
        name="CHANNEL_HANGUP",
        direction="inbound",
        caller="2348012345678",
        callee="support",
        subscriber_id="sub-3",
    )

    deliveries = dispatch_call_event(db_session, event)

    assert deliveries == []
