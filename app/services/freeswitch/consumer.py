"""Background ESL consumer.

At app startup this keeps an :class:`EslBridge` connected to FreeSWITCH and
feeds every normalized call event into the webhook dispatch, so the
``call.ringing`` / ``call.answered`` / ``call.ended`` webhooks actually fire to
the CRM in production (previously nothing installed a handler, so they never
did). Runs in a daemon thread and reconnects with backoff. ESL stays bound to
127.0.0.1 on the FreeSWITCH host — this consumer connects locally to it.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.config import settings
from app.db import SessionLocal
from app.services.events.dispatch import dispatch_and_enqueue
from app.services.freeswitch.esl import CallEvent, EslBridge

logger = logging.getLogger(__name__)

DEFAULT_RECONNECT_BACKOFF_SECONDS = 5.0


def handle_event(event: CallEvent) -> None:
    """Persist + enqueue webhooks for one call event.

    Runs inside the ESL stream thread, so it must never raise back into it.
    """
    try:
        db = SessionLocal()
        try:
            dispatch_and_enqueue(db, event)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("ESL event dispatch failed for %s", getattr(event, "name", "?"))


def _default_bridge() -> EslBridge:
    return EslBridge(
        host=settings.esl_host,
        port=settings.esl_port,
        password=settings.esl_password,
    )


class EslConsumer:
    """Owns a daemon thread that keeps an EslBridge connected and dispatching."""

    def __init__(
        self,
        bridge_factory: Callable[[], EslBridge] | None = None,
        handler: Callable[[CallEvent], None] | None = None,
        backoff_seconds: float = DEFAULT_RECONNECT_BACKOFF_SECONDS,
    ) -> None:
        self._factory = bridge_factory or _default_bridge
        self._handler = handler or handle_event
        self._backoff = backoff_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="esl-consumer", daemon=True)
        self._thread.start()
        logger.info("ESL consumer started")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def run_once(self) -> None:
        """One connect-and-serve cycle: connect the bridge, install the handler,
        and block (letting greenswitch stream events to the handler) until the
        connection drops or stop() is called."""
        bridge = self._factory()
        bridge.on_event(self._handler)
        bridge.connect()
        logger.info("ESL consumer connected (%s:%s)", settings.esl_host, settings.esl_port)
        while not self._stop.is_set() and bridge.is_alive():
            self._stop.wait(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.warning(
                    "ESL connection failed; reconnecting in %ss", self._backoff, exc_info=True
                )
            if not self._stop.is_set():
                self._stop.wait(timeout=self._backoff)
