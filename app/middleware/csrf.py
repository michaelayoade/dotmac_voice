"""CSRF middleware for browser form requests.

Implements a double-submit cookie check:
- Sets a CSRF cookie and `request.state.csrf_token` on safe requests.
- Validates form submissions by matching submitted token to cookie.
"""

from __future__ import annotations

import re
import secrets
from hmac import compare_digest
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

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

# FIX 1: Body-size cap — bound the request body we buffer for token extraction.
# Sourced from the same upload limit used elsewhere in the app.
_MAX_CSRF_BODY_BYTES: int = settings.upload_max_size_bytes


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

    async def _submitted_token(self, scope: Scope, receive: Receive) -> tuple[str, bytes | None]:
        request = Request(scope, receive)
        header_token = request.headers.get("X-CSRF-Token", "")
        if header_token:
            return header_token, None

        # FIX 1: Check Content-Length before buffering to avoid DoS
        content_length_str = request.headers.get("content-length", "")
        if content_length_str:
            try:
                content_length = int(content_length_str)
            except ValueError:
                content_length = 0
            if content_length > _MAX_CSRF_BODY_BYTES:
                return "", None  # sentinel: will be caught as 413 by caller

        # Buffer body with a hard cap — defense against missing/lying Content-Length
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > _MAX_CSRF_BODY_BYTES:
                # Signal overflow to caller; return sentinel body=None
                return "", None  # 413 sentinel
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return _extract_token_from_body(body, ctype), body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        csrf_token, should_set_cookie = self._ensure_token(request)
        scope.setdefault("state", {})["csrf_token"] = csrf_token
        replay_receive: Receive = receive

        if self._requires_csrf(request):
            # FIX 1: Check Content-Length before buffering for DoS protection
            content_length_str = request.headers.get("content-length", "")
            if content_length_str:
                try:
                    content_length = int(content_length_str)
                except ValueError:
                    content_length = 0
                if content_length > _MAX_CSRF_BODY_BYTES:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "code": "request_too_large",
                            "message": "Request body exceeds maximum allowed size",
                            "details": None,
                        },
                    )
                    await response(scope, receive, send)
                    return

            header_token = request.headers.get("X-CSRF-Token", "")
            if header_token:
                submitted_token = header_token
                consumed_body = None
            else:
                # Buffer body with hard cap — defense against missing/lying Content-Length
                chunks: list[bytes] = []
                total = 0
                oversized = False
                while True:
                    message = await receive()
                    chunk = message.get("body", b"")
                    total += len(chunk)
                    if total > _MAX_CSRF_BODY_BYTES:
                        oversized = True
                        break
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break

                if oversized:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "code": "request_too_large",
                            "message": "Request body exceeds maximum allowed size",
                            "details": None,
                        },
                    )
                    await response(scope, receive, send)
                    return

                consumed_body = b"".join(chunks)
                ctype = (
                    request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                submitted_token = _extract_token_from_body(consumed_body, ctype)

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
