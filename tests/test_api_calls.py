"""Tests for the click-to-dial endpoint (POST /calls/dial)."""

import pytest
from fastapi.testclient import TestClient

from app.services.freeswitch.esl import build_originate_command

INGRESS_KEY = "test-ingress-key"
HEADERS = {"X-API-Key": INGRESS_KEY}


# ---------------------------------------------------------------------------
# Pure function: build_originate_command
# ---------------------------------------------------------------------------


def test_build_originate_command_with_caller_id():
    cmd = build_originate_command(
        agent_extension="1001",
        destination="08012345678",
        domain="c1.local",
        caller_id_number="09000000000",
    )
    assert cmd == (
        "bgapi originate {origination_caller_id_number=09000000000}"
        "user/1001@c1.local 08012345678 XML default"
    )


def test_build_originate_command_without_caller_id():
    cmd = build_originate_command(
        agent_extension="1001",
        destination="08012345678",
        domain="c1.local",
    )
    assert cmd == "bgapi originate user/1001@c1.local 08012345678 XML default"


# ---------------------------------------------------------------------------
# Fake ESL bridge for tests
# ---------------------------------------------------------------------------


class FakeEslBridge:
    """Records originate calls without touching a real FreeSWITCH socket."""

    def __init__(self):
        self.calls: list[str] = []

    def originate(self, command: str) -> str:
        self.calls.append(command)
        return "ok"


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_esl():
    return FakeEslBridge()


@pytest.fixture()
def dial_client(db_session, fake_esl):
    """Test client with ESL override."""
    from app.api.calls import get_esl_bridge
    from app.api.deps import get_db as api_get_db
    from app.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[api_get_db] = override_get_db
    app.dependency_overrides[get_esl_bridge] = lambda: fake_esl

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


def test_dial_allowed_domestic_originates(dial_client, fake_esl):
    """Domestic call is allowed: ESL originate is invoked once with the correct command.

    The response must NOT leak the ESL command string; verify the command via the fake ESL.
    """
    resp = dial_client.post(
        "/calls/dial",
        json={
            "agent_extension": "1001",
            "destination": "08012345678",
            "domain": "c1.local",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "originating"
    assert body["classification"] == "domestic"
    # Response must NOT include the internal ESL command (security: no SIP URI leakage).
    assert "command" not in body
    expected_cmd = "bgapi originate user/1001@c1.local 08012345678 XML default"
    assert len(fake_esl.calls) == 1
    assert fake_esl.calls[0] == expected_cmd


def test_dial_international_blocked_does_not_originate(dial_client, fake_esl):
    """International call with allow_international=false → 403; ESL not called."""
    resp = dial_client.post(
        "/calls/dial",
        json={
            "agent_extension": "1001",
            "destination": "+14155550123",
            "domain": "c1.local",
            "allow_international": False,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 403
    body = resp.json()
    # Error handler wraps HTTPException.detail into body["message"]
    assert body["message"] == "international_blocked"
    assert fake_esl.calls == []


def test_dial_caged_scope_blocks(dial_client, fake_esl):
    """allowed_destinations restricts calls; non-matching destination → 403."""
    resp = dial_client.post(
        "/calls/dial",
        json={
            "agent_extension": "1001",
            "destination": "2348012345678",
            "domain": "c1.local",
            "allowed_destinations": ["support"],
        },
        headers=HEADERS,
    )
    assert resp.status_code == 403
    body = resp.json()
    # Error handler wraps HTTPException.detail into body["message"]
    assert body["message"] == "not_in_allowlist"
    assert fake_esl.calls == []


def test_dial_requires_ingress(dial_client):
    """Missing API key → 401."""
    resp = dial_client.post(
        "/calls/dial",
        json={
            "agent_extension": "1001",
            "destination": "08012345678",
            "domain": "c1.local",
        },
        # No X-API-Key header
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Security: ESL command-string injection tests
# ---------------------------------------------------------------------------


def test_dial_rejects_injection_in_agent_extension(dial_client, fake_esl):
    """agent_extension with whitespace/newline injects a second ESL command → 422, ESL not called."""
    resp = dial_client.post(
        "/calls/dial",
        json={
            "agent_extension": "1001 XML default\nbgapi originate user/evil@x 900 XML default",
            "destination": "08012345678",
            "domain": "c1.local",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 422
    assert fake_esl.calls == []


def test_dial_rejects_injection_in_domain(dial_client, fake_esl):
    """domain with a space → 422, ESL not called."""
    resp = dial_client.post(
        "/calls/dial",
        json={
            "agent_extension": "1001",
            "destination": "08012345678",
            "domain": "c1.local foo",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 422
    assert fake_esl.calls == []


def test_dial_rejects_bad_caller_id(dial_client, fake_esl):
    """caller_id_number with non-digit/non-plus chars → 422, ESL not called."""
    resp = dial_client.post(
        "/calls/dial",
        json={
            "agent_extension": "1001",
            "destination": "08012345678",
            "domain": "c1.local",
            "caller_id_number": "123 abc",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 422
    assert fake_esl.calls == []
