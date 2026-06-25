"""Tests for notification API endpoints."""

import uuid

from app.models.notification import Notification, NotificationType


class TestNotificationAPI:
    def test_create_notification(self, client, auth_headers, person):
        payload = {
            "recipient_id": str(person.id),
            "title": "API Test",
            "message": "Test message",
            "type": "info",
        }
        response = client.post("/notifications", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "API Test"
        assert data["is_read"] is False

    def test_list_my_notifications(self, client, auth_headers, db_session, person):
        n = Notification(
            recipient_id=person.id,
            title="My Notification",
            type=NotificationType.info,
        )
        db_session.add(n)
        db_session.commit()

        response = client.get("/notifications/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) >= 1

    def test_get_unread_count(self, client, auth_headers, db_session, person):
        for i in range(2):
            db_session.add(
                Notification(
                    recipient_id=person.id,
                    title=f"Unread {i}",
                    type=NotificationType.info,
                )
            )
        db_session.commit()

        response = client.get("/notifications/me/unread-count", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 2

    def test_mark_read(self, client, auth_headers, db_session, person):
        n = Notification(
            recipient_id=person.id,
            title="Mark Read",
            type=NotificationType.info,
        )
        db_session.add(n)
        db_session.commit()
        db_session.refresh(n)

        response = client.post(f"/notifications/me/{n.id}/read", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["is_read"] is True

    def test_mark_read_not_found(self, client, auth_headers):
        response = client.post(
            f"/notifications/me/{uuid.uuid4()}/read", headers=auth_headers
        )
        assert response.status_code == 404

    def test_mark_all_read(self, client, auth_headers, db_session, person):
        for i in range(3):
            db_session.add(
                Notification(
                    recipient_id=person.id,
                    title=f"All Read {i}",
                    type=NotificationType.info,
                )
            )
        db_session.commit()

        response = client.post("/notifications/me/read-all", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["marked_read"] >= 3

    def test_unauthenticated(self, client):
        response = client.get("/notifications/me")
        assert response.status_code == 401
