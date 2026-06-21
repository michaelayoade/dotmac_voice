"""Tests for ESL bridge and event normalization."""

from app.services.freeswitch.esl import normalize_event


def test_normalize_channel_answer():
    """Test normalization of CHANNEL_ANSWER event."""
    raw = {
        "Event-Name": "CHANNEL_ANSWER",
        "Unique-ID": "abc-123",
        "Call-Direction": "inbound",
        "Caller-Caller-ID-Number": "2348012345678",
        "Caller-Destination-Number": "support",
        "variable_dotmac_subscriber_id": "subscriber-9",
    }
    ev = normalize_event(raw)
    assert ev.call_uuid == "abc-123" and ev.name == "CHANNEL_ANSWER"
    assert ev.direction == "inbound" and ev.subscriber_id == "subscriber-9"


def test_normalize_ignores_unknown_event():
    """Test that unknown events are ignored (return None)."""
    assert normalize_event({"Event-Name": "RE_SCHEDULE"}) is None
