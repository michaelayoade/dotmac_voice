"""WebSocket endpoint for real-time notifications."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.services.auth_dependencies import resolve_active_session_person_id
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()
_WS_SESSION_RECHECK_SECONDS = 30.0


def _authenticate_ws(token: str) -> str | None:
    """Validate the JWT *and its backing session* and return person_id or None.

    Uses the same session validation as HTTP auth so a logged-out / revoked /
    expired session cannot open or keep a WebSocket open until JWT expiry.
    """
    if not token:
        return None
    db: Session = SessionLocal()
    try:
        return resolve_active_session_person_id(db, token)
    except Exception:
        logger.exception("WebSocket authentication failed")
        return None
    finally:
        db.close()


def _extract_ws_token(websocket: WebSocket) -> str:
    """Read JWT token from Sec-WebSocket-Protocol header."""
    raw_header = websocket.headers.get("sec-websocket-protocol", "")
    if not raw_header:
        return ""
    for protocol in raw_header.split(","):
        protocol = protocol.strip()
        if protocol:
            return protocol
    return ""


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time notification push.

    Authenticate via Sec-WebSocket-Protocol header.
    """
    token = _extract_ws_token(websocket)
    person_id_str = await run_in_threadpool(_authenticate_ws, token)
    if not person_id_str:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    from uuid import UUID

    person_id = UUID(person_id_str)
    await ws_manager.connect(person_id, websocket)
    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=_WS_SESSION_RECHECK_SECONDS,
                )
            except TimeoutError:
                if await run_in_threadpool(_authenticate_ws, token) != person_id_str:
                    await websocket.close(code=4001, reason="Unauthorized")
                    return
                continue
            if await run_in_threadpool(_authenticate_ws, token) != person_id_str:
                await websocket.close(code=4001, reason="Unauthorized")
                return
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket connection failed")
    finally:
        ws_manager.disconnect(person_id, websocket)
