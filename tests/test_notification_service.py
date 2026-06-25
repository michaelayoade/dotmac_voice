"""Tests for notification service."""

import uuid

import pytest

from app.schemas.notification import NotificationCreate
from app.services.notification import NotificationService


@pytest.fixture()
def notification_service(db_session):
    return NotificationService(db_session)


class TestNotificationService:
    def test_create_notification(self, notification_service, db_session, person):
        data = NotificationCreate(
            recipient_id=person.id,
            title="Test Notification",
            message="Hello there",
            type="info",
        )
        record = notification_service.create(data)
        db_session.commit()
        assert record.id is not None
        assert record.title == "Test Notification"
        assert record.recipient_id == person.id
        assert record.is_read is False

    def test_get_by_id(self, notification_service, db_session, person):
        data = NotificationCreate(
            recipient_id=person.id,
            title="Find Me",
        )
        record = notification_service.create(data)
        db_session.commit()
        found = notification_service.get_by_id(record.id)
        assert found is not None
        assert found.id == record.id

    def test_get_by_id_not_found(self, notification_service):
        result = notification_service.get_by_id(uuid.uuid4())
        assert result is None

    def test_list_for_recipient(self, notification_service, db_session, person):
        for i in range(3):
            notification_service.create(
                NotificationCreate(
                    recipient_id=person.id,
                    title=f"Notification {i}",
                )
            )
        db_session.commit()

        items = notification_service.list_for_recipient(person.id)
        assert len(items) >= 3

    def test_list_unread_only(self, notification_service, db_session, person):
        notification_service.create(
            NotificationCreate(
                recipient_id=person.id,
                title="Unread",
            )
        )
        n2 = notification_service.create(
            NotificationCreate(
                recipient_id=person.id,
                title="Read",
            )
        )
        db_session.commit()

        notification_service.mark_read(n2.id, person.id)
        db_session.commit()

        unread = notification_service.list_for_recipient(person.id, unread_only=True)
        assert all(not n.is_read for n in unread)

    def test_unread_count(self, notification_service, db_session, person):
        initial = notification_service.unread_count(person.id)
        notification_service.create(
            NotificationCreate(
                recipient_id=person.id,
                title="Count Me",
            )
        )
        notification_service.create(
            NotificationCreate(
                recipient_id=person.id,
                title="Count Me Too",
            )
        )
        db_session.commit()
        assert notification_service.unread_count(person.id) == initial + 2

    def test_mark_read(self, notification_service, db_session, person):
        record = notification_service.create(
            NotificationCreate(
                recipient_id=person.id,
                title="Read Me",
            )
        )
        db_session.commit()
        assert record.is_read is False

        result = notification_service.mark_read(record.id, person.id)
        db_session.commit()
        assert result is not None
        assert result.is_read is True
        assert result.read_at is not None

    def test_mark_read_wrong_recipient(self, notification_service, db_session, person):
        record = notification_service.create(
            NotificationCreate(
                recipient_id=person.id,
                title="Not Yours",
            )
        )
        db_session.commit()
        result = notification_service.mark_read(record.id, uuid.uuid4())
        assert result is None

    def test_mark_all_read(self, notification_service, db_session, person):
        for i in range(3):
            notification_service.create(
                NotificationCreate(
                    recipient_id=person.id,
                    title=f"Mark All {i}",
                )
            )
        db_session.commit()

        count = notification_service.mark_all_read(person.id)
        db_session.commit()
        assert count >= 3
        assert notification_service.unread_count(person.id) == 0

    def test_create_with_entity(self, notification_service, db_session, person):
        record = notification_service.create(
            NotificationCreate(
                recipient_id=person.id,
                title="Entity Linked",
                entity_type="person",
                entity_id=str(person.id),
                action_url="/people/" + str(person.id),
            )
        )
        db_session.commit()
        assert record.entity_type == "person"
        assert record.action_url is not None
