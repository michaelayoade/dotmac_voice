"""Tests for the WebSocket notification endpoint."""

import uuid
from unittest.mock import AsyncMock

import pytest

import app.api.ws as ws_api


class _FakeWebSocket:
    def __init__(self, token: str):
        self.headers = {"sec-websocket-protocol": token}
        self.receive_text = AsyncMock()
        self.send_text = AsyncMock()
        self.close = AsyncMock()


class _FakeWebSocketManager:
    def __init__(self):
        self.connected = []
        self.disconnected = []

    async def connect(self, person_id, websocket):
        self.connected.append((person_id, websocket))

    def disconnect(self, person_id, websocket):
        self.disconnected.append((person_id, websocket))


@pytest.mark.asyncio
async def test_ws_revalidates_session_and_disconnects_on_revocation(monkeypatch):
    person_id = uuid.uuid4()
    token = "jwt-token"
    auth_results = iter([str(person_id), str(person_id), None])
    websocket = _FakeWebSocket(token)
    websocket.receive_text.side_effect = ["ping", "ping"]
    manager = _FakeWebSocketManager()

    async def immediate_threadpool(func, *args):
        return func(*args)

    def fake_authenticate(seen_token):
        assert seen_token == token
        return next(auth_results)

    monkeypatch.setattr(ws_api, "run_in_threadpool", immediate_threadpool)
    monkeypatch.setattr(ws_api, "_authenticate_ws", fake_authenticate)
    monkeypatch.setattr(ws_api, "ws_manager", manager)

    await ws_api.ws_notifications(websocket)

    websocket.send_text.assert_awaited_once_with("pong")
    websocket.close.assert_awaited_once_with(code=4001, reason="Unauthorized")
    assert manager.connected == [(person_id, websocket)]
    assert manager.disconnected == [(person_id, websocket)]
