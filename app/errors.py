"""Structured error handlers with request_id correlation.

Every error response includes a consistent envelope:
    {
        "code": "error_code",
        "message": "Human-readable message",
        "details": null | object,
        "request_id": "uuid"
    }
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.services.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str:
    """Extract request_id set by ObservabilityMiddleware."""
    return getattr(request.state, "request_id", "unknown")


def _error_payload(code: str, message: str, details: object, request_id: str) -> dict:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id,
    }


def register_error_handlers(app: object) -> None:
    @app.exception_handler(BadRequestError)  # type: ignore[arg-type]
    async def bad_request_error_handler(
        request: Request, exc: BadRequestError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=400,
            content=_error_payload("bad_request", str(exc), None, request_id),
        )

    @app.exception_handler(ConflictError)  # type: ignore[arg-type]
    async def conflict_error_handler(
        request: Request, exc: ConflictError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=409,
            content=_error_payload("conflict", str(exc), None, request_id),
        )

    @app.exception_handler(NotFoundError)  # type: ignore[arg-type]
    async def not_found_error_handler(
        request: Request, exc: NotFoundError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=404,
            content=_error_payload("not_found", str(exc), None, request_id),
        )

    @app.exception_handler(RateLimitError)  # type: ignore[arg-type]
    async def rate_limit_error_handler(
        request: Request, exc: RateLimitError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=429,
            content=_error_payload("rate_limited", str(exc), None, request_id),
        )

    @app.exception_handler(ServiceUnavailableError)  # type: ignore[arg-type]
    async def service_unavailable_error_handler(
        request: Request, exc: ServiceUnavailableError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=503,
            content=_error_payload("service_unavailable", str(exc), None, request_id),
        )

    @app.exception_handler(HTTPException)  # type: ignore[arg-type]
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        detail = exc.detail
        code = f"http_{exc.status_code}"
        message = "Request failed"
        details = None
        if isinstance(detail, dict):
            code = detail.get("code", code)
            message = detail.get("message", message)
            details = detail.get("details")
        elif isinstance(detail, str):
            message = detail
        else:
            details = detail
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, message, details, request_id),
        )

    @app.exception_handler(RequestValidationError)  # type: ignore[arg-type]
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning(
            "Validation error on %s %s: %s",
            request.method,
            request.url.path,
            exc.errors(),
            extra={"request_id": request_id},
        )
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                "validation_error",
                "Validation error",
                # jsonable_encoder handles non-serializable ctx (e.g. the ValueError
                # a model_validator/field_validator raises lands in ctx['error']).
                jsonable_encoder(exc.errors()),
                request_id,
            ),
        )

    @app.exception_handler(Exception)  # type: ignore[arg-type]
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.exception(
            "Unhandled exception on %s %s",
            request.method,
            request.url.path,
            extra={"request_id": request_id},
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                "internal_error",
                "Internal server error",
                None,
                request_id,
            ),
        )
