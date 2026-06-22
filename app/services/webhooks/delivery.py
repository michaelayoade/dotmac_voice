"""Outbound webhook delivery service for CRM notifications."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.webhook import DeliveryStatus, WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)


def sign_body(secret: str, body: bytes) -> str:
    """Return HMAC-SHA256 signature in 'sha256=<hex>' format."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def endpoints_for_event(db: Session, event_type: str) -> list[WebhookEndpoint]:
    """Return active endpoints subscribed to the given event_type."""
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))
    all_active = list(db.scalars(stmt).all())
    return [ep for ep in all_active if event_type in (ep.event_types or [])]


def attempt_delivery(
    db: Session,
    delivery: WebhookDelivery,
    timeout: float = 5.0,
    max_attempts: int = 5,
) -> bool:
    """POST the delivery payload to the endpoint.

    Returns True if delivery succeeded (2xx), False otherwise.
    Increments attempts and sets status=failed when max_attempts is reached.
    """
    endpoint: WebhookEndpoint | None = db.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None:
        logger.error("Endpoint %s not found for delivery %s", delivery.endpoint_id, delivery.id)
        delivery.attempts += 1
        delivery.last_error = "endpoint not found"
        if delivery.attempts >= max_attempts:
            delivery.status = DeliveryStatus.failed
        db.flush()
        return False

    body: bytes = json.dumps(delivery.payload).encode()
    sig = sign_body(endpoint.secret, body)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": delivery.event_type,
        "X-Webhook-Delivery-Id": str(delivery.id),
        "X-Webhook-Signature-256": sig,
    }

    delivered = False
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(endpoint.url, content=body, headers=headers)

        delivery.attempts += 1
        if resp.is_success:
            delivery.status = DeliveryStatus.delivered
            delivered = True
        else:
            delivery.last_status_code = resp.status_code
            if delivery.attempts >= max_attempts:
                delivery.status = DeliveryStatus.failed

    except httpx.TransportError as exc:
        delivery.attempts += 1
        delivery.last_error = str(exc)[:512]
        if delivery.attempts >= max_attempts:
            delivery.status = DeliveryStatus.failed

    db.flush()
    return delivered
