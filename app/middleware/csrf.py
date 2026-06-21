"""CSRF middleware for browser form requests.

Implements a double-submit cookie check:
- Sets a CSRF cookie and `request.state.csrf_token` on safe requests.
- Validates form submissions by matching submitted token to cookie.
"""

from __future__ import annotations

import re
import secrets
from hmac import compare_digest

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_FORM_CONTENT_TYPES = {
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
}
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,}$")


def _is_secure_request(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "")
    return proto == "https" or request.url.scheme == "https"


def _is_valid_token(token: str) -> bool:
    """Check if token matches the expected format."""
    return bool(_TOKEN_PATTERN.fullmatch(token))


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
        cookie_name: str = "csrf_token",
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
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

    async def _submitted_token(self, request: Request) -> str:
        header_token = request.headers.get("X-CSRF-Token", "")
        if header_token:
            return header_token
        form = await request.form()
        token = form.get("csrf_token")
        return str(token) if token else ""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        csrf_token, should_set_cookie = self._ensure_token(request)
        request.state.csrf_token = csrf_token

        if self._requires_csrf(request):
            submitted_token = await self._submitted_token(request)

            # Validate submitted token exists and has correct format
            if not submitted_token or not _is_valid_token(submitted_token):
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_invalid",
                        "message": "CSRF token missing or invalid",
                        "details": None,
                    },
                )

            # csrf_token is already validated in _ensure_token()
            # Use constant-time comparison for the actual token match
            if not compare_digest(submitted_token, csrf_token):
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "csrf_invalid",
                        "message": "CSRF token missing or invalid",
                        "details": None,
                    },
                )

        response: Response = await call_next(request)  # type: ignore[call-arg]
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
