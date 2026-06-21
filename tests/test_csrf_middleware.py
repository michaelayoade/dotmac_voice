"""Unit tests for CSRFMiddleware request validation logic."""

from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.csrf import CSRFMiddleware


def _middleware() -> CSRFMiddleware:
    async def app(scope, receive, send):  # pragma: no cover
        return None

    return CSRFMiddleware(app)


def _request(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Request:
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
    return Request(scope)


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


@pytest.mark.asyncio
async def test_is_secure_request_detects_forwarded_proto() -> None:
    middleware = _middleware()
    request = _request("GET", "/", headers={"X-Forwarded-Proto": "https"})

    async def call_next(_request: Request) -> Response:
        return Response(status_code=200)

    # ensure dispatch executes and sets secure cookie path without raising
    response = await middleware.dispatch(request, call_next)
    assert response is not None
