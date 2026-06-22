from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from threading import Lock
from time import monotonic
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.auth_flow import router as auth_flow_router
from app.api.persons import router as people_router
from app.api.rbac import router as rbac_router
from app.api.scheduler import router as scheduler_router
from app.api.settings import router as settings_router
from app.config import settings, validate_settings
from app.db import SessionLocal
from app.errors import register_error_handlers
from app.logging import configure_logging
from app.middleware.csrf import CSRFMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.models.domain_settings import DomainSetting, SettingDomain
from app.observability import ObservabilityMiddleware
from app.services import audit as audit_service
from app.services.settings_seed import (
    seed_audit_settings,
    seed_auth_settings,
    seed_billing_settings,
    seed_scheduler_settings,
)
from app.telemetry import setup_otel
from app.web_home import router as web_home_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[arg-type]
    # ── Startup ──────────────────────────────────────────
    # Validate configuration
    warnings = validate_settings(settings)
    for warning in warnings:
        if warning.critical:
            raise RuntimeError(f"Critical configuration error: {warning.message}")
        logger.warning("Config warning: %s", warning.message)

    # Seed default settings
    db = SessionLocal()
    try:
        seed_auth_settings(db)
        seed_audit_settings(db)
        seed_scheduler_settings(db)
        seed_billing_settings(db)
        db.commit()
    finally:
        db.close()

    logger.info("Application started (pid=%s)", os.getpid())
    yield

    # ── Shutdown ─────────────────────────────────────────
    logger.info("Application shutting down")


app = FastAPI(title="DotMac Voice API", lifespan=lifespan)

_AUDIT_SETTINGS_CACHE: dict[str, Any] | None = None
_AUDIT_SETTINGS_CACHE_AT: float | None = None
_AUDIT_SETTINGS_CACHE_TTL_SECONDS = 30.0
_AUDIT_SETTINGS_LOCK = Lock()
configure_logging()
setup_otel(app)

# ── Middleware (order matters: last added = first executed) ──
register_error_handlers(app)

# CORS — must be added before other middleware
cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-CSRF-Token",
            "X-Requested-With",
        ],
        expose_headers=["X-Request-Id", "X-RateLimit-Remaining"],
    )

trusted_hosts = [
    host.strip()
    for host in getattr(settings, "trusted_hosts", "").split(",")
    if host.strip()
]
if trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)


@app.middleware("http")
async def static_cache_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        cache_control = (
            "public, max-age=31536000, immutable"
            if "v" in request.query_params
            else settings.static_cache_control
        )
        response.headers.setdefault("Cache-Control", cache_control)
    return response


@app.middleware("http")
async def audit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response: Response
    path = request.url.path
    try:
        db = SessionLocal()
        try:
            audit_settings = _load_audit_settings(db)
        finally:
            db.close()
    except Exception:
        logger.exception(
            "Failed to load audit settings, skipping audit for %s %s",
            request.method,
            path,
        )
        return await call_next(request)
    if not audit_settings["enabled"]:
        return await call_next(request)
    header_key = audit_settings.get("read_trigger_header") or ""
    header_value = request.headers.get(header_key, "") if header_key else ""
    track_read = request.method == "GET" and (
        (header_value or "").lower() == "true"
        or request.query_params.get(audit_settings["read_trigger_query"]) == "true"
    )
    should_log = request.method in audit_settings["methods"] or track_read
    if _is_audit_path_skipped(path, audit_settings["skip_paths"]):
        should_log = False
    try:
        response = await call_next(request)
    except Exception:
        if should_log:
            db = SessionLocal()
            try:
                audit_service.audit_events.log_request(
                    db, request, Response(status_code=500)
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to log audit event for %s %s",
                    request.method,
                    path,
                )
            finally:
                db.close()
        raise
    if should_log:
        db = SessionLocal()
        try:
            audit_service.audit_events.log_request(db, request, response)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to log audit event for %s %s",
                request.method,
                path,
            )
        finally:
            db.close()
    return response


def _load_audit_settings(db: Session) -> dict[str, Any]:
    global _AUDIT_SETTINGS_CACHE, _AUDIT_SETTINGS_CACHE_AT
    now = monotonic()
    with _AUDIT_SETTINGS_LOCK:
        if (
            _AUDIT_SETTINGS_CACHE
            and _AUDIT_SETTINGS_CACHE_AT
            and now - _AUDIT_SETTINGS_CACHE_AT < _AUDIT_SETTINGS_CACHE_TTL_SECONDS
        ):
            return _AUDIT_SETTINGS_CACHE
    defaults: dict[str, Any] = {
        "enabled": True,
        "methods": {"POST", "PUT", "PATCH", "DELETE"},
        "skip_paths": ["/static", "/web", "/health"],
        "read_trigger_header": "x-audit-read",
        "read_trigger_query": "audit",
    }
    stmt = select(DomainSetting).where(
        DomainSetting.domain == SettingDomain.audit,
        DomainSetting.is_active.is_(True),
    )
    rows = list(db.scalars(stmt).all())
    values = {row.key: row for row in rows}
    if "enabled" in values:
        defaults["enabled"] = _to_bool(values["enabled"])
    if "methods" in values:
        defaults["methods"] = _to_list(values["methods"], upper=True)
    if "skip_paths" in values:
        defaults["skip_paths"] = _to_list(values["skip_paths"], upper=False)
    if "read_trigger_header" in values:
        defaults["read_trigger_header"] = _to_str(values["read_trigger_header"])
    if "read_trigger_query" in values:
        defaults["read_trigger_query"] = _to_str(values["read_trigger_query"])
    with _AUDIT_SETTINGS_LOCK:
        _AUDIT_SETTINGS_CACHE = defaults
        _AUDIT_SETTINGS_CACHE_AT = now
    return defaults


def _to_bool(setting: DomainSetting) -> bool:
    value = setting.value_json if setting.value_json is not None else setting.value_text
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _to_str(setting: DomainSetting) -> str:
    value = setting.value_text if setting.value_text is not None else setting.value_json
    if value is None:
        return ""
    return str(value)


def _to_list(setting: DomainSetting, upper: bool) -> set[str] | list[str]:
    value = setting.value_json if setting.value_json is not None else setting.value_text
    items: list[str]
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        items = []
    if upper:
        return {item.upper() for item in items}
    return items


def _is_audit_path_skipped(path: str, skip_paths: list[str]) -> bool:
    return any(path.startswith(prefix) for prefix in skip_paths)


def _include_api_router(router: object, dependencies: list[Any] | None = None) -> None:
    app.include_router(router, dependencies=dependencies)  # type: ignore[arg-type]
    app.include_router(router, prefix="/api/v1", dependencies=dependencies)  # type: ignore[arg-type]


from app.api.billing import router as billing_router  # noqa: E402
from app.api.calls import router as calls_router  # noqa: E402
from app.api.cdr import router as cdr_router  # noqa: E402
from app.api.deps import require_role  # noqa: E402
from app.api.file_uploads import router as file_uploads_router  # noqa: E402
from app.api.notifications import router as notifications_router  # noqa: E402
from app.api.provisioning import router as provisioning_router  # noqa: E402
from app.api.tokens import router as tokens_router  # noqa: E402
from app.api.webhooks import router as webhooks_router  # noqa: E402
from app.api.ws import router as ws_router  # noqa: E402
from app.web.audit import router as web_audit_router  # noqa: E402
from app.web.auth import router as web_auth_router  # noqa: E402
from app.web.billing.coupons import router as web_billing_coupons_router  # noqa: E402
from app.web.billing.customers import (  # noqa: E402
    router as web_billing_customers_router,
)
from app.web.billing.entitlements import (  # noqa: E402
    router as web_billing_entitlements_router,
)
from app.web.billing.invoices import router as web_billing_invoices_router  # noqa: E402
from app.web.billing.payment_methods import (  # noqa: E402
    router as web_billing_payment_methods_router,
)
from app.web.billing.prices import router as web_billing_prices_router  # noqa: E402
from app.web.billing.products import router as web_billing_products_router  # noqa: E402
from app.web.billing.subscriptions import (  # noqa: E402
    router as web_billing_subscriptions_router,
)
from app.web.billing.webhook_events import (  # noqa: E402
    router as web_billing_webhook_events_router,
)
from app.web.dashboard import router as web_dashboard_router  # noqa: E402
from app.web.deps import WebAuthRedirect  # noqa: E402
from app.web.file_uploads import router as web_file_uploads_router  # noqa: E402
from app.web.notifications import router as web_notifications_router  # noqa: E402
from app.web.people import router as web_people_router  # noqa: E402
from app.web.permissions import router as web_permissions_router  # noqa: E402
from app.web.roles import router as web_roles_router  # noqa: E402
from app.web.scheduler import router as web_scheduler_router  # noqa: E402
from app.web.settings import router as web_settings_router  # noqa: E402

_include_api_router(auth_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(auth_flow_router)
_include_api_router(rbac_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(people_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(audit_router)
_include_api_router(settings_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(scheduler_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(billing_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(file_uploads_router)
_include_api_router(notifications_router, dependencies=[Depends(require_role("admin"))])
_include_api_router(provisioning_router)
_include_api_router(calls_router)
_include_api_router(cdr_router)
_include_api_router(webhooks_router)
_include_api_router(tokens_router)
app.include_router(ws_router)
app.include_router(web_auth_router)
app.include_router(web_dashboard_router)
app.include_router(web_people_router)
app.include_router(web_roles_router)
app.include_router(web_permissions_router)
app.include_router(web_settings_router)
app.include_router(web_scheduler_router)
app.include_router(web_audit_router)
app.include_router(web_notifications_router)
app.include_router(web_file_uploads_router)
app.include_router(web_billing_products_router)
app.include_router(web_billing_prices_router)
app.include_router(web_billing_customers_router)
app.include_router(web_billing_subscriptions_router)
app.include_router(web_billing_invoices_router)
app.include_router(web_billing_payment_methods_router)
app.include_router(web_billing_coupons_router)
app.include_router(web_billing_entitlements_router)
app.include_router(web_billing_webhook_events_router)
app.include_router(web_home_router)


@app.exception_handler(WebAuthRedirect)
async def web_auth_redirect_handler(request: Request, exc: WebAuthRedirect) -> Response:
    """Redirect to login page when web auth fails."""
    from starlette.responses import RedirectResponse

    next_url = exc.next_url or request.url.path
    return RedirectResponse(url=f"/admin/login?next={next_url}", status_code=302)


app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Health Checks ────────────────────────────────────────


@app.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe — always returns ok if the process is running."""
    return {"status": "ok"}


@app.get("/health/ready")
def readiness_check() -> JSONResponse:
    """Readiness probe — verifies database and Redis connectivity."""
    checks: dict[str, str] = {}

    # Check database
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        finally:
            db.close()
    except Exception:
        logger.exception("Database health check failed")
        checks["database"] = "unavailable"

    # Check Redis
    try:
        import redis as redis_lib

        r = redis_lib.Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=2
        )
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        logger.exception("Redis health check failed")
        checks["redis"] = "unavailable"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )


@app.get("/metrics")
def metrics(request: Request) -> Response:
    metrics_token = settings.metrics_token
    client_host = request.client.host if request.client else "unknown"
    if metrics_token:
        auth_header = request.headers.get("Authorization", "")
        expected = f"Bearer {metrics_token}"
        if not auth_header or not secrets.compare_digest(auth_header, expected):
            logger.warning(
                "Unauthorized /metrics access from %s: missing or invalid bearer token",
                client_host,
            )
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    elif client_host != "127.0.0.1":
        logger.warning(
            "Unauthorized /metrics access from %s: loopback-only access",
            client_host,
        )
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
