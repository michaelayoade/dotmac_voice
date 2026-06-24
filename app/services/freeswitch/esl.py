"""ESL bridge for FreeSWITCH event streaming."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_RELEVANT = {"CHANNEL_CREATE", "CHANNEL_ANSWER", "CHANNEL_HANGUP", "CHANNEL_HANGUP_COMPLETE"}


def reloadxml(host: str, port: int, password: str) -> None:  # pragma: no cover - touches a real ESL socket
    """Trigger a FreeSWITCH ``reloadxml`` over ESL so config writes go live.

    Best-effort: connects to the FreeSWITCH Event Socket, issues ``reloadxml``,
    and closes the connection. The DB is the source of truth, so callers should
    treat any failure here as non-fatal.

    Args:
        host: ESL server host.
        port: ESL server port.
        password: ESL server password.
    """
    import greenswitch

    conn = greenswitch.InboundESL(host=host, port=port, password=password)
    conn.connect()
    try:
        conn.send("api reloadxml")
    finally:
        # greenswitch InboundESL does not expose a public close; drop the socket if present.
        sock = getattr(conn, "sock", None)
        if sock is not None:
            sock.close()


def command(host: str, port: int, password: str, cmd: str) -> None:  # pragma: no cover - touches a real ESL socket
    """Issue an arbitrary FreeSWITCH ``api`` command over ESL (best-effort).

    Used for runtime config the DB-write + reloadxml path can't do (e.g.
    ``callcenter_config queue load``). DB is the source of truth; failures are
    non-fatal and should be caught by the caller.
    """
    import greenswitch

    conn = greenswitch.InboundESL(host=host, port=port, password=password)
    conn.connect()
    try:
        conn.send(f"api {cmd}")
    finally:
        sock = getattr(conn, "sock", None)
        if sock is not None:
            sock.close()


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

    def originate(self, command: str) -> str:  # pragma: no cover - exercised in integration, not unit tests
        """Open a short-lived ESL connection, send an originate command, and close.

        Click-to-dial is a one-off action and ``get_esl_bridge()`` hands out a
        fresh, *unconnected* bridge per request, so this manages its own
        connection (connect -> send -> close) instead of relying on the
        long-lived event connection (``self._conn``), which is not present here.
        Mirrors :func:`reloadxml`.

        Args:
            command: A FreeSWITCH ESL command string (e.g. built by build_originate_command).

        Returns:
            The raw response string from FreeSWITCH ESL.
        """
        import greenswitch

        conn = greenswitch.InboundESL(
            host=self._host, port=self._port, password=self._password
        )
        conn.connect()
        try:
            response = conn.send(command)
            return getattr(response, "data", "") or str(response)
        finally:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                sock.close()

    def is_alive(self) -> bool:  # pragma: no cover - depends on live greenswitch conn
        """Best-effort liveness of the streaming connection.

        Returns False once connected and the underlying socket reports closed;
        defaults to True when liveness can't be determined, so the consumer
        doesn't reconnect spuriously.
        """
        conn = self._conn
        if conn is None:
            return False
        return bool(getattr(conn, "connected", True))

    def _dispatch(self, event) -> None:  # pragma: no cover
        """Dispatch normalized event to callback."""
        normalized = normalize_event(dict(event.headers))
        if normalized and self._callback:
            self._callback(normalized)


def build_originate_command(
    agent_extension: str,
    destination: str,
    domain: str,
    caller_id_number: str = "",
) -> str:
    """Build a FreeSWITCH bgapi originate command bridging an agent extension to a destination.

    Args:
        agent_extension: The agent's extension number (e.g. "1001").
        destination: The normalized destination number to dial.
        domain: The SIP domain (e.g. "c1.local").
        caller_id_number: Optional outbound caller ID number.
            If empty, the {origination_caller_id_number=...} vars block is omitted.

    Returns:
        A FreeSWITCH ESL originate command string.
    """
    if caller_id_number:
        vars_block = f"{{origination_caller_id_number={caller_id_number}}}"
    else:
        vars_block = ""
    return f"bgapi originate {vars_block}user/{agent_extension}@{domain} {destination} XML default"
