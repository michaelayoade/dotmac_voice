"""CSRF middleware for browser form requests.

Implements a double-submit cookie check:
- Sets a CSRF cookie and `request.state.csrf_token` on safe requests.
- Validates form submissions by matching submitted token to cookie.
"""

from __future__ import annotations

import re
import secrets
from hmac import compare_digest
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_FORM_CONTENT_TYPES = {
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
}
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,}$")
_MULTIPART_TOKEN_PATTERN = re.compile(
    rb'name="csrf_token"(?:\r\n[^\r\n]*)*\r\n\r\n([A-Za-z0-9_-]{32,})'
)


def _is_secure_request(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "")
    return proto == "https" or request.url.scheme == "https"


def _is_valid_token(token: str) -> bool:
    """Check if token matches the expected format."""
    return bool(_TOKEN_PATTERN.fullmatch(token))


def _extract_token_from_body(body: bytes, content_type: str) -> str:
    if content_type == "multipart/form-data":
        match = _MULTIPART_TOKEN_PATTERN.search(body)
        if match:
            return match.group(1).decode("ascii")
        return ""
    if content_type in {"application/x-www-form-urlencoded", "text/plain"}:
        parsed = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
        values = parsed.get("csrf_token")
        return values[0] if values else ""
    return ""


def _replay_body_receive(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class CSRFMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        cookie_name: str = "csrf_token",
    ) -> None:
        self.app = app
        self.cookie_name = cookie_name

    def _ensure_token(self, request: Request) -> tuple[str, bool]:
        token = request.cookies.get(self.cookie_name, "")
        if token and _is_valid_token(token):
            return token, False
        return secrets.token_urlsafe(32), True

    def _is_exempt_path(self, path: str) -> bool:
        return (
            path.startswith("/static")
            or path.startswith("/health")
            or path == "/metrics"
        )

    def _requires_csrf(self, request: Request) -> bool:
        if request.method in _SAFE_METHODS:
            return False
        if self._is_exempt_path(request.url.path):
            return False
        ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return ctype in _FORM_CONTENT_TYPES

    async def _submitted_token(self, request: Request) -> tuple[str, bytes | None]:
        header_token = request.headers.get("X-CSRF-Token", "")
        if header_token:
            return header_token, None
        body = await request.body()
        ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return _extract_token_from_body(body, ctype), body

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Compatibility helper for direct unit tests.

        Runtime requests use the ASGI ``__call__`` path so consumed request
        bodies can be replayed to downstream form handlers.
        """
        csrf_token, should_set_cookie = self._ensure_token(request)
        request.state.csrf_token = csrf_token
        response: Response = await call_next(request)
        if should_set_cookie:
            response.set_cookie(
                key=self.cookie_name,
                value=csrf_token,
                httponly=True,
                secure=_is_secure_request(request),
                samesite="lax",
                path="/",
            )
        return response

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        csrf_token, should_set_cookie = self._ensure_token(request)
        scope.setdefault("state", {})["csrf_token"] = csrf_token
        replay_receive: Receive = receive

        if self._requires_csrf(request):
            submitted_token, consumed_body = await self._submitted_token(request)
            if consumed_body is not None:
                replay_receive = _replay_body_receive(consumed_body)

            # Validate submitted token exists and has correct format
            if not submitted_token or not _is_valid_token(submitted_token):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_invalid",
                        "message": "CSRF token missing or invalid",
                        "details": None,
                    },
                )
                await response(scope, replay_receive, send)
                return

            # csrf_token is already validated in _ensure_token()
            # Use constant-time comparison for the actual token match
            if not compare_digest(submitted_token, csrf_token):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_invalid",
                        "message": "CSRF token missing or invalid",
                        "details": None,
                    },
                )
                await response(scope, replay_receive, send)
                return

        async def send_with_cookie(message: Message) -> None:
            if should_set_cookie and message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                cookie_response = Response()
                cookie_response.set_cookie(
                    key=self.cookie_name,
                    value=csrf_token,
                    httponly=True,
                    secure=_is_secure_request(request),
                    samesite="lax",
                    path="/",
                )
                headers.extend(
                    header
                    for header in cookie_response.raw_headers
                    if header[0].lower() == b"set-cookie"
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, replay_receive, send_with_cookie)
