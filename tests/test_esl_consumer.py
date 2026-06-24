"""Tests for the background ESL->webhook consumer."""
import threading
from unittest.mock import MagicMock

from app.services.freeswitch import consumer as consumer_mod
from app.services.freeswitch.consumer import EslConsumer
from app.services.freeswitch.esl import CallEvent

EVENT = CallEvent(
    call_uuid="abc",
    name="CHANNEL_ANSWER",
    direction="inbound",
    caller="1001",
    callee="1002",
    subscriber_id=None,
)


class FakeBridge:
    def __init__(self, alive=False):
        self.connected = False
        self.handler = None
        self._alive = alive

    def on_event(self, cb):
        self.handler = cb

    def connect(self):
        self.connected = True

    def is_alive(self):
        return self._alive


class TestHandleEvent:
    def test_dispatches_and_commits(self, monkeypatch):
        fake_db = MagicMock()
        monkeypatch.setattr(consumer_mod, "SessionLocal", lambda: fake_db)
        seen = {}
        monkeypatch.setattr(
            consumer_mod, "dispatch_and_enqueue",
            lambda db, event: seen.setdefault("event", event) or [],
        )
        consumer_mod.handle_event(EVENT)
        assert seen["event"] is EVENT
        fake_db.commit.assert_called_once()
        fake_db.close.assert_called_once()

    def test_swallows_exceptions(self, monkeypatch):
        # SessionLocal raising must not propagate into the ESL stream thread.
        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(consumer_mod, "SessionLocal", boom)
        consumer_mod.handle_event(EVENT)  # no exception

    def test_closes_db_even_if_dispatch_raises(self, monkeypatch):
        fake_db = MagicMock()
        monkeypatch.setattr(consumer_mod, "SessionLocal", lambda: fake_db)

        def boom(db, event):
            raise RuntimeError("dispatch fail")

        monkeypatch.setattr(consumer_mod, "dispatch_and_enqueue", boom)
        consumer_mod.handle_event(EVENT)  # swallowed
        fake_db.close.assert_called_once()


class TestEslConsumer:
    def test_run_once_connects_and_installs_handler(self):
        bridge = FakeBridge(alive=False)  # serve loop exits immediately
        handler = object()
        c = EslConsumer(bridge_factory=lambda: bridge, handler=handler, lock_path=None)
        c.run_once()
        assert bridge.connected is True
        assert bridge.handler is handler

    def test_singleton_lock_blocks_second_consumer(self, tmp_path):
        lock = str(tmp_path / "esl.lock")
        c1 = EslConsumer(bridge_factory=lambda: FakeBridge(), handler=lambda e: None, lock_path=lock)
        c2 = EslConsumer(bridge_factory=lambda: FakeBridge(), handler=lambda e: None, lock_path=lock)
        assert c1._acquire_lock() is True
        assert c2._acquire_lock() is False  # c1 holds the lock
        c1._release_lock()
        assert c2._acquire_lock() is True  # freed -> failover
        c2._release_lock()

    def test_run_reconnects_on_failure(self):
        attempts = {"n": 0}
        c_ref = {}

        def factory():
            attempts["n"] += 1
            if attempts["n"] >= 2:
                c_ref["c"].stop()  # break the loop after the second attempt
            raise RuntimeError("connect failed")

        c = EslConsumer(bridge_factory=factory, handler=lambda e: None, backoff_seconds=0.01, lock_path=None)
        c_ref["c"] = c
        c._run()
        assert attempts["n"] >= 2  # it retried after the first failure

    def test_start_stop_lifecycle(self):
        started = threading.Event()

        def factory():
            started.set()
            return FakeBridge(alive=True)

        c = EslConsumer(bridge_factory=factory, handler=lambda e: None, backoff_seconds=0.01, lock_path=None)
        c.start()
        assert started.wait(2.0)
        c.stop(timeout=2.0)
