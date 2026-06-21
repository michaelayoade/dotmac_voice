# DotMac Voice — Claude Agent Guide

FastAPI + SQLAlchemy 2.0 + Jinja2/HTMX/Alpine.js + Celery + PostgreSQL. Port 8001.
Cloned from `dotmac_starter` (remote kept as `starter-upstream` for pulling template updates).

## What this is

**Voice control-plane service** for DotMac telephony. It owns ALL coupling to a
**locally-hosted FusionPBX / FreeSWITCH** (kept on-net so RTP/media latency stays low;
this service reaches it over the LAN, and remote callers reach FreeSWITCH directly for media).

Responsibilities:
- **Provisioning** — create/update FusionPBX domains, extensions, voicemail, IVR, queues (REST/DB).
- **ESL event bridge** — subscribe to FreeSWITCH Event Socket, normalize call events.
- **CDR access** — expose rated/ratable call detail records.
- **WebRTC/SIP token minting** — short-lived ephemeral credentials for in-app "Talk to an agent"
  and agent softphones (no permanent SIP passwords handed out).
- **Dialplan/queue management** — support queues, routing, locked-down customer origination.
- Exposes **REST + webhooks** consumed by other services.

## Who consumes it (it is NOT customer-facing)

Server-to-server, via API keys. No business customer or agent logs into THIS app directly.
- **dotmac_sub** — `reconcile_voice` pushes desired voice state here; pulls CDRs for rating →
  invoices (`LedgerCategory.voice_service`). Customers manage voice in sub's selfcare "Phone" tab
  + the native app's in-app calling.
- **dotmac_crm** — call-center voice channel: this service POSTs call events to crm's
  `/api/v1/crm/inbox/webhooks/voice`; crm's click-to-dial calls back here.

## Single-tenant — by design

This is a control-plane service, NOT a multi-tenant SaaS. Tenancy is handled BELOW it:
FusionPBX domains isolate customers; dotmac_sub owns customer accounts/billing.
Here, a customer is **data** — a `customer_id` / `fusionpbx_domain` foreign key on models —
NOT an app-level `org_id` scope. Do not add org-scoping middleware.

## Identity is shared with the rest of the platform

dotmac_sub and dotmac_crm share customer records. Pass the subscriber id on calls so the
agent gets an exact screen-pop (no caller-ID guessing).

## Non-Negotiable Rules (house style — keep)
- SQLAlchemy 2.0: `select()` + `scalars()`, never `db.query()`
- `db.flush()` in services, NOT `db.commit()` — routes commit (`_commit()` / `_commit_and_refresh()`)
- Services raise `DomainError` subclasses from `app.services.exceptions`; let app-level handlers translate
- Routes are thin wrappers — no business logic, no DB queries, no aggregation inside
- Sync routes (`def`, not `async`); background work → Celery
- Pydantic v2 (`ConfigDict(from_attributes=True)`); UUID primary keys
- Type hints on all functions (mypy passes); `logger = logging.getLogger(__name__)` per file
- Credentials/secrets encrypted via `credential_crypto`; idempotent migrations
- SQLite in-memory for tests
- Commands: always `poetry run ruff`, `poetry run mypy`, `poetry run pytest` (or `make` targets)

## Template Rules
- Single quotes on `x-data` with `tojson`; `{{ var if var else '' }}` not `| default('')`
- Dict lookup for dynamic Tailwind classes; `| safe` only for CSRF/`tojson`/admin CSS
- `status_badge()`, `empty_state()`, `live_search()` macros — never inline
- Every `{% for %}` needs `{% else %}` + `empty_state()`; CSRF on every POST form
- `<div id="results-container">` on list pages; `scope="col"` on `<th>`; pair `bg-white dark:bg-slate-800`

## Service Pattern
```python
class SomeService:
    def __init__(self, db: Session):
        self.db = db
    def create(self, data) -> Model:
        record = Model(**data.model_dump())
        self.db.add(record)
        self.db.flush()
        return record
```

## Telephony notes (as they're built)
- FusionPBX/FreeSWITCH config writes follow the reconcile pattern (compute desired vs actual → delta).
- Lock down customer-originated WebRTC: support queue only, never arbitrary outbound (toll-fraud guard).
- Postgres ENUM extensions (e.g. new channel/category values) need a non-transactional
  `ALTER TYPE ... ADD VALUE` migration — they cannot roll back inside a transaction.

## Security
- Never bare `except:`; never `| safe` on user content
- File uploads via `FileUploadService` only; `resolve_safe_path()` for all path operations
