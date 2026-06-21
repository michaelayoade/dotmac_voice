"""Unit tests for CSRFMiddleware request validation logic."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from app.middleware.csrf import (
    CSRFMiddleware,
    _MAX_CSRF_BODY_BYTES,
    _extract_token_from_body,
)

# ---------------------------------------------------------------------------
# Helpers for unit-testing internal helpers (no HTTP stack needed)
# ---------------------------------------------------------------------------


def _middleware() -> CSRFMiddleware:
    async def app(scope, receive, send):  # pragma: no cover
        return None

    return CSRFMiddleware(app)


def _request(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> StarletteRequest:
    raw_headers: list[tuple[bytes, bytes]] = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    if cookies:
        cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie.encode("latin-1")))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return StarletteRequest(scope)


# ---------------------------------------------------------------------------
# Helper: build a minimal FastAPI app wired with CSRFMiddleware for __call__
# tests.  The probe route echoes the posted body so we can assert
# _replay_body_receive actually works.
# ---------------------------------------------------------------------------


def _build_probe_app(max_body_bytes: int | None = None) -> FastAPI:
    probe = FastAPI()
    probe.add_middleware(CSRFMiddleware)

    @probe.post("/_csrf_probe")
    async def csrf_probe(request: Request):
        body = await request.body()
        return JSONResponse({"body": body.decode("utf-8", errors="replace")}, status_code=200)

    return probe


# A valid token satisfying _TOKEN_PATTERN: ^[A-Za-z0-9_-]{32,}$
_VALID_TOKEN = "A" * 32
_COOKIE_NAME = "csrf_token"

# ---------------------------------------------------------------------------
# _requires_csrf / _ensure_token / helper unit tests (no ASGI stack)
# ---------------------------------------------------------------------------


def test_requires_csrf_for_form_post() -> None:
    middleware = _middleware()
    request = _request(
        "POST", "/settings/branding", headers={"Content-Type": "multipart/form-data"}
    )
    assert middleware._requires_csrf(request) is True


def test_does_not_require_csrf_for_json_post() -> None:
    middleware = _middleware()
    request = _request("POST", "/people", headers={"Content-Type": "application/json"})
    assert middleware._requires_csrf(request) is False


def test_does_not_require_csrf_for_safe_method() -> None:
    middleware = _middleware()
    request = _request("GET", "/settings/branding")
    assert middleware._requires_csrf(request) is False


def test_does_not_require_csrf_for_exempt_path() -> None:
    middleware = _middleware()
    request = _request("POST", "/health", headers={"Content-Type": "text/plain"})
    assert middleware._requires_csrf(request) is False


def test_ensure_token_reuses_cookie_token() -> None:
    middleware = _middleware()
    request = _request("GET", "/", cookies={"csrf_token": "a" * 32})
    token, should_set = middleware._ensure_token(request)
    assert token == "a" * 32
    assert should_set is False


def test_ensure_token_generates_when_missing() -> None:
    middleware = _middleware()
    request = _request("GET", "/")
    token, should_set = middleware._ensure_token(request)
    assert len(token) >= 32
    assert should_set is True


# ---------------------------------------------------------------------------
# FIX 2: Tests exercising the real __call__ ASGI path via TestClient
# ---------------------------------------------------------------------------


class TestCSRFCallPath:
    """Drive requests through CSRFMiddleware.__call__ (the runtime path)."""

    def _client(self) -> TestClient:
        return TestClient(_build_probe_app(), raise_server_exceptions=True)

    # ------------------------------------------------------------------
    # POST with NO csrf token -> 403
    # ------------------------------------------------------------------
    def test_post_without_csrf_token_is_rejected(self) -> None:
        client = self._client()
        response = client.post(
            "/_csrf_probe",
            data={"foo": "bar"},
            # no cookies, no token field
        )
        assert response.status_code == 403
        assert response.json()["code"] == "csrf_invalid"

    # ------------------------------------------------------------------
    # POST with valid double-submit cookie+field -> 200, body replayed
    # ------------------------------------------------------------------
    def test_post_with_valid_token_passes_and_body_replayed(self) -> None:
        client = self._client()
        response = client.post(
            "/_csrf_probe",
            data={"csrf_token": _VALID_TOKEN, "extra": "hello"},
            cookies={_COOKIE_NAME: _VALID_TOKEN},
        )
        assert response.status_code == 200
        # _replay_body_receive must have delivered the body to the handler
        body_text = response.json()["body"]
        assert "extra=hello" in body_text

    # ------------------------------------------------------------------
    # POST with cookie token != form token -> 403
    # ------------------------------------------------------------------
    def test_post_with_mismatched_tokens_is_rejected(self) -> None:
        client = self._client()
        different_token = "B" * 32
        response = client.post(
            "/_csrf_probe",
            data={"csrf_token": _VALID_TOKEN},
            cookies={_COOKIE_NAME: different_token},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "csrf_invalid"

    # ------------------------------------------------------------------
    # POST with body exceeding cap -> 413
    # ------------------------------------------------------------------
    def test_post_with_oversized_body_rejected_with_413(self) -> None:
        client = self._client()
        oversized_body = "x=" + "A" * (_MAX_CSRF_BODY_BYTES + 1)
        response = client.post(
            "/_csrf_probe",
            content=oversized_body.encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(oversized_body.encode())),
            },
            cookies={_COOKIE_NAME: _VALID_TOKEN},
        )
        assert response.status_code == 413

    # ------------------------------------------------------------------
    # POST with body at exactly the cap -> passes CSRF check normally
    # (token still matters, but 413 must NOT fire at-cap)
    # ------------------------------------------------------------------
    def test_post_at_cap_not_rejected_by_size_check(self) -> None:
        """Body exactly at _MAX_CSRF_BODY_BYTES must not trigger 413."""
        client = self._client()
        # craft a form body exactly at the cap with the token present
        token_field = f"csrf_token={_VALID_TOKEN}&"
        padding_needed = _MAX_CSRF_BODY_BYTES - len(token_field.encode())
        if padding_needed < 0:
            padding_needed = 0
        body = token_field + "p=" + "A" * max(0, padding_needed - 2)
        body_bytes = body.encode()[:_MAX_CSRF_BODY_BYTES]
        response = client.post(
            "/_csrf_probe",
            content=body_bytes,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body_bytes)),
            },
            cookies={_COOKIE_NAME: _VALID_TOKEN},
        )
        # Should NOT be 413; will be 200 or 403 depending on token extraction
        assert response.status_code != 413


# ---------------------------------------------------------------------------
# FIX 2 + FIX 3: Verify dispatch() no longer exists on CSRFMiddleware
# ---------------------------------------------------------------------------


def test_dispatch_method_removed() -> None:
    """dispatch() is a dead footgun under add_middleware; it must be gone."""
    assert not hasattr(CSRFMiddleware, "dispatch"), (
        "CSRFMiddleware.dispatch() should have been removed (Fix 3)"
    )


# ---------------------------------------------------------------------------
# FIX 1: _MAX_CSRF_BODY_BYTES constant is exported and sane
# ---------------------------------------------------------------------------


def test_max_csrf_body_bytes_is_positive_int() -> None:
    assert isinstance(_MAX_CSRF_BODY_BYTES, int)
    assert _MAX_CSRF_BODY_BYTES > 0


@pytest.mark.asyncio
async def test_is_secure_request_detected_via_forwarded_proto() -> None:
    """Verify X-Forwarded-Proto: https is recognised as a secure request."""
    from app.middleware.csrf import _is_secure_request

    request = _request("GET", "/", headers={"X-Forwarded-Proto": "https"})
    assert _is_secure_request(request) is True
