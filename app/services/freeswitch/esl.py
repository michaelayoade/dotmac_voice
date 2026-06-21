"""ESL bridge for FreeSWITCH event streaming."""

from dataclasses import dataclass

_RELEVANT = {"CHANNEL_CREATE", "CHANNEL_ANSWER", "CHANNEL_HANGUP", "CHANNEL_HANGUP_COMPLETE"}


@dataclass(frozen=True)
class CallEvent:
    """Normalized call event from FreeSWITCH."""

    call_uuid: str
    name: str
    direction: str
    caller: str
    callee: str
    subscriber_id: str | None


def normalize_event(raw: dict) -> CallEvent | None:
    """
    Map FreeSWITCH event headers to CallEvent.

    Args:
        raw: Raw event dict from FreeSWITCH headers.

    Returns:
        CallEvent if the event is relevant, None otherwise.
    """
    name = raw.get("Event-Name", "")
    if name not in _RELEVANT:
        return None
    return CallEvent(
        call_uuid=raw.get("Unique-ID", ""),
        name=name,
        direction=raw.get("Call-Direction", ""),
        caller=raw.get("Caller-Caller-ID-Number", ""),
        callee=raw.get("Caller-Destination-Number", ""),
        subscriber_id=raw.get("variable_dotmac_subscriber_id") or None,
    )


class EslBridge:
    """Thin wrapper over greenswitch.InboundESL for ESL event handling."""

    def __init__(self, host: str, port: int, password: str) -> None:
        """Initialize ESL bridge.

        Args:
            host: ESL server host.
            port: ESL server port.
            password: ESL server password.
        """
        self._host = host
        self._port = port
        self._password = password
        self._conn = None
        self._callback = None

    def on_event(self, callback) -> None:
        """Register callback for normalized events.

        Args:
            callback: Callable that receives CallEvent objects.
        """
        self._callback = callback

    def connect(self) -> None:  # pragma: no cover - exercised in integration, not unit tests
        """Connect to FreeSWITCH ESL server and subscribe to events."""
        import greenswitch

        self._conn = greenswitch.InboundESL(
            host=self._host, port=self._port, password=self._password
        )
        self._conn.connect()
        self._conn.register_handle("*", self._dispatch)
        self._conn.send("events plain ALL")

    def _dispatch(self, event) -> None:  # pragma: no cover
        """Dispatch normalized event to callback."""
        normalized = normalize_event(dict(event.headers))
        if normalized and self._callback:
            self._callback(normalized)
