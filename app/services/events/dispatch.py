"""Dispatch normalized FreeSWITCH ESL call events to outbound CRM webhooks."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.webhook import DeliveryStatus, WebhookDelivery
from app.services.freeswitch.esl import CallEvent
from app.services.webhooks.delivery import endpoints_for_event

logger = logging.getLogger(__name__)

# Map FreeSWITCH event names to webhook event types.
# CHANNEL_HANGUP is intentionally absent — we emit exactly one call.ended from
# CHANNEL_HANGUP_COMPLETE and don't double-fire.
_EVENT_TYPE_MAP: dict[str, str] = {
    "CHANNEL_CREATE": "call.ringing",
    "CHANNEL_ANSWER": "call.answered",
    "CHANNEL_HANGUP_COMPLETE": "call.ended",
}


def event_type_for(name: str) -> str | None:
    """Return the webhook event type for a FreeSWITCH event name, or None if unmapped."""
    return _EVENT_TYPE_MAP.get(name)


def build_event_payload(event: CallEvent, event_type: str) -> dict:
    """Build the JSON payload for a webhook delivery from a normalized call event."""
    return {
        "event_type": event_type,
        "event_name": event.name,
        "call_uuid": event.call_uuid,
        "direction": event.direction,
        "caller": event.caller,
        "callee": event.callee,
        "subscriber_id": event.subscriber_id,
    }


def dispatch_call_event(db: Session, event: CallEvent) -> list[WebhookDelivery]:
    """Create WebhookDelivery rows for all endpoints subscribed to the event.

    Does NOT call httpx or enqueue Celery tasks — this function is intentionally
    side-effect-free beyond DB writes so it remains unit-testable without a broker.

    Args:
        db: Active SQLAlchemy session. Caller is responsible for commit.
        event: Normalized call event from FreeSWITCH.

    Returns:
        List of created (flushed, not yet committed) WebhookDelivery objects.
        Empty list if the event name is unmapped or no endpoints are subscribed.
    """
    et = event_type_for(event.name)
    if et is None:
        return []

    payload = build_event_payload(event, et)
    endpoints = endpoints_for_event(db, et)

    deliveries: list[WebhookDelivery] = []
    for ep in endpoints:
        delivery = WebhookDelivery(
            endpoint_id=ep.id,
            event_type=et,
            payload=payload,
            status=DeliveryStatus.pending,
        )
        db.add(delivery)
        deliveries.append(delivery)

    if deliveries:
        db.flush()

    return deliveries


def dispatch_and_enqueue(db: Session, event: CallEvent) -> list[WebhookDelivery]:
    """Create WebhookDelivery rows and enqueue Celery tasks for each.

    Wraps dispatch_call_event and enqueues app.tasks.webhook_deliver.deliver
    for each created delivery. Celery broker failures are logged and swallowed —
    the delivery row stays in status=pending for a sweep job to retry.

    Args:
        db: Active SQLAlchemy session. Caller is responsible for commit.
        event: Normalized call event from FreeSWITCH.

    Returns:
        List of created WebhookDelivery objects (same as dispatch_call_event).
    """
    # Import lazily so this module is importable without a running broker.
    from app.tasks.webhook_deliver import deliver  # noqa: PLC0415

    deliveries = dispatch_call_event(db, event)

    for d in deliveries:
        try:
            deliver.delay(str(d.id))
        except Exception:
            logger.exception(
                "Failed to enqueue webhook delivery %s for event %s — "
                "row stays pending for sweep",
                d.id,
                event.name,
            )

    return deliveries
