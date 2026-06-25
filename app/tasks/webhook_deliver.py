"""Celery task: deliver a webhook to its registered endpoint with retry/backoff."""
from __future__ import annotations

import logging
import uuid

from app.celery_app import celery_app
from app.models.webhook import DeliveryStatus, WebhookDelivery

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.webhook_deliver.deliver", bind=True, max_retries=5)
def deliver(self, delivery_id: str) -> None:  # type: ignore[override]
    """Load delivery and attempt to POST to the registered endpoint.

    Retries with exponential backoff (capped at 300 s) if not delivered.
    Leaves status=failed after max_attempts reached.
    """
    from app.db import SessionLocal
    from app.services.webhooks.delivery import attempt_delivery

    db = SessionLocal()
    try:
        delivery: WebhookDelivery | None = db.get(WebhookDelivery, uuid.UUID(delivery_id))
        if delivery is None:
            logger.warning("Delivery %s not found, skipping", delivery_id)
            return

        ok = attempt_delivery(db, delivery)
        db.commit()

        if not ok and delivery.status != DeliveryStatus.failed:
            countdown = min(2 ** self.request.retries, 300)
            raise self.retry(countdown=countdown)

    except self.MaxRetriesExceededError:
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.webhook_deliver.sweep")
def sweep() -> int:
    """Scheduled durable retry: re-attempt webhook deliveries stuck in pending.

    Enable by creating a ScheduledTask(task_name="app.tasks.webhook_deliver.sweep",
    interval_seconds=120). Returns the number of deliveries re-attempted.
    """
    from app.db import SessionLocal
    from app.services.webhooks.delivery import sweep_deliveries

    db = SessionLocal()
    try:
        count = sweep_deliveries(db)
        db.commit()
        return count
    finally:
        db.close()
