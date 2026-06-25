"""Notification service — create, list, mark read, unread count."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationType
from app.schemas.notification import NotificationCreate
from app.services.exceptions import NotFoundError

logger = logging.getLogger(__name__)


class NotificationService:
    """Manages notification records."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, data: NotificationCreate) -> Notification:
        """Create a new notification."""
        notification = Notification(
            recipient_id=data.recipient_id,
            sender_id=data.sender_id,
            title=data.title,
            message=data.message,
            type=NotificationType(data.type) if data.type else NotificationType.info,
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            action_url=data.action_url,
        )
        self.db.add(notification)
        self.db.flush()
        logger.info("Created notification for %s: %s", data.recipient_id, data.title)
        return notification

    def get_by_id(self, notification_id: UUID) -> Notification | None:
        """Get a notification by ID."""
        return self.db.get(Notification, notification_id)

    def list_for_recipient(
        self,
        recipient_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Notification]:
        """List notifications for a recipient."""
        stmt = select(Notification).where(
            Notification.recipient_id == recipient_id,
            Notification.is_active.is_(True),
        )
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.scalars(stmt).all())

    def unread_count(self, recipient_id: UUID) -> int:
        """Count unread notifications for a recipient."""
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.is_active.is_(True),
                Notification.is_read.is_(False),
            )
        )
        result = self.db.execute(stmt).scalar()
        return result or 0

    def mark_read(
        self, notification_id: UUID, recipient_id: UUID
    ) -> Notification | None:
        """Mark a single notification as read."""
        notification = self.db.get(Notification, notification_id)
        if not notification or notification.recipient_id != recipient_id:
            return None
        notification.is_read = True
        notification.read_at = datetime.now(UTC)
        self.db.flush()
        return notification

    def mark_read_or_404(
        self, notification_id: UUID, recipient_id: UUID
    ) -> Notification:
        notification = self.mark_read(notification_id, recipient_id)
        if notification is None:
            raise NotFoundError("Notification not found")
        return notification

    def mark_all_read(self, recipient_id: UUID) -> int:
        """Mark all notifications as read for a recipient. Returns count updated."""
        stmt = (
            update(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.is_active.is_(True),
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=datetime.now(UTC))
        )
        result = self.db.execute(stmt)
        self.db.flush()
        count = result.rowcount  # type: ignore[union-attr]
        logger.info("Marked %d notifications as read for %s", count, recipient_id)
        return count
