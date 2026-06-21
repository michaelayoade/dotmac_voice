# Tier 0 — Core & Edge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the local telephony engine + public WebRTC edge, prove one real on-net call, and build the `dotmac_voice` control-plane skeleton (FusionPBX client, provisioning + `reconcile_voice`, authenticated ingress, ESL bridge, token minting).

**Architecture:** Local FreeSWITCH (call/media) + FusionPBX (provisioning/admin) on a private core box; Kamailio + RTPengine + coturn on a public edge box so internet WebRTC clients reach the local engine. `dotmac_voice` (FastAPI, runs local beside FreeSWITCH) provisions FusionPBX via REST and controls/observes calls via ESL; it exposes an authenticated public HTTPS API consumed by dotmac_sub/dotmac_crm. See `docs/superpowers/specs/2026-06-21-dotmac-voice-architecture-design.md`.

**Tech Stack:** FreeSWITCH, FusionPBX, Kamailio, RTPengine, coturn; FastAPI + SQLAlchemy 2.0 + Alembic + Celery (Python 3.12); `httpx` (FusionPBX client), `greenswitch` (ESL), `respx` (test HTTP mock).

## Global Constraints

- Python service follows the house style verbatim: routes are thin wrappers; **`flush()` in services, `commit()` only via the route-local `_commit()`/`_commit_and_refresh()` helpers**; services raise `DomainError` subclasses from `app.services.exceptions`; sync routes (`def`); UUID PKs; SQLAlchemy 2.0 `select()`; Pydantic v2 `ConfigDict`.
- Settings are a `@dataclass(frozen=True)` reading `os.getenv(...)`; **every new setting must be mirrored as a plain attribute in `tests/conftest.py:MockSettings`**.
- Models use `from sqlalchemy.dialects.postgresql import UUID` + `UUID(as_uuid=True)` and `sa.Enum(PyEnum)` (SQLite-tolerant, matches existing `app/models/person.py`).
- Migrations are idempotent (`inspector.has_table(...)`, `ENUM(..., create_type=False).create(conn, checkfirst=True)`); **every new model module must be imported in `alembic/env.py`**.
- API routers are registered via `_include_api_router(...)` (mounts at both `/` and `/api/v1`).
- Test command: `poetry run pytest tests/ -q` (SQLite in-memory; fixtures `client`, `auth_headers`, `db_session` from `tests/conftest.py`). Commit after every green task.
- ESL is **local-only** (never bound to a public interface). The public API ingress uses constant-time API-key verification + trusted-proxy-aware IP allowlist + edge rate limiting. mTLS is the preferred production deployment posture; if deferred, document the compensating controls before exposing `voice.dotmac.io`.

---

# PART 1 — Infrastructure & Edge (ops tasks)

> These tasks are an ops runbook. Their "test" is a functional check (a process is up, a call completes), not pytest. Each ends with an explicit verification gate. Target OS: Debian/Ubuntu LTS. Two hosts: **CORE** (private, on-net) and **EDGE** (public IP, DMZ).

### Task 1: Provision hosts, network & firewall

**Deliverable:** CORE and EDGE hosts reachable; firewall rules in place; DNS for the edge.

- [ ] **Step 1: Allocate hosts.** CORE (private IP, e.g. `10.x`) and EDGE (public IP + private NIC on the same L2/L3 as CORE). Record both IPs.
- [ ] **Step 2: DNS.** Create `sip.dotmac.io` and `voice.dotmac.io` A-records → EDGE public IP (sip = SIP/WSS edge; voice = the dotmac_voice API ingress reverse proxy).
- [ ] **Step 3: Firewall — EDGE (public).** Allow inbound: `443/tcp` (WSS + API ingress), `5060-5061/udp+tcp` (SIP), `5080/tcp` if needed, `3478/udp+tcp` + `5349/tcp` (coturn), `30000-40000/udp` (RTPengine media). Deny all else.
- [ ] **Step 4: Firewall — CORE (private).** Allow from EDGE private IP only: `5060/udp` (FreeSWITCH SIP), RTP range `16384-32768/udp`. Allow from dotmac_voice host: FusionPBX `8080`, ESL `8021` (localhost/LAN only — never from EDGE/public).
- [ ] **Step 5: Verify.** From EDGE: `nc -vz <CORE_PRIVATE_IP> 5060` succeeds. From public internet: `nc -vz <EDGE_PUBLIC_IP> 5060` succeeds, `nc -vz <CORE_PRIVATE_IP> 5060` fails (CORE not publicly reachable).

### Task 2: Install FreeSWITCH on CORE

**Deliverable:** FreeSWITCH running, `fs_cli` connects, default profiles loaded.

- [ ] **Step 1: Install** from the SignalWire FreeSWITCH packages (token-based apt repo per current FreeSWITCH docs) or distro package: `apt-get install -y freeswitch-meta-all`.
- [ ] **Step 2: Set ESL password.** Edit `/etc/freeswitch/autoload_configs/event_socket.conf.xml`: set `listen-ip` to `127.0.0.1` (local-only) and `password` to a strong value; record it for `ESL_PASSWORD`.
- [ ] **Step 3: Start + enable.** `systemctl enable --now freeswitch`.
- [ ] **Step 4: Verify.** `fs_cli -x 'status'` returns uptime; `fs_cli -x 'sofia status'` lists the `internal` profile as RUNNING.

### Task 3: Install FusionPBX on CORE (on top of FreeSWITCH)

**Deliverable:** FusionPBX web UI reachable on the private net; one test domain + two extensions registered locally.

- [ ] **Step 1: Install** via the official FusionPBX install script (PostgreSQL backend, nginx, php-fpm). Bind nginx/API to the private IP only.
- [ ] **Step 2: Create a test domain** in the FusionPBX UI (e.g. `test.local`).
- [ ] **Step 3: Create two extensions** (`1001`, `1002`) with passwords in the test domain.
- [ ] **Step 4: Enable the API.** In FusionPBX, create an API key / app credential for `dotmac_voice`; record it for `FUSIONPBX_API_KEY` and the base URL for `FUSIONPBX_API_URL`.
- [ ] **Step 5: Verify.** Register a LAN softphone (e.g. Zoiper) as `1001@test.local` against CORE directly; `fs_cli -x 'sofia status profile internal reg'` shows the registration. Call `1002` from `1001` on the LAN — two-way audio.

### Task 4: Install RTPengine + Kamailio on EDGE

**Deliverable:** Kamailio running as a WSS-capable SIP proxy/registrar that routes to CORE FreeSWITCH; RTPengine relaying media.

- [ ] **Step 1: Install RTPengine** (`apt-get install -y ngcp-rtpengine` or build). Configure `interface=<EDGE_PUBLIC_IP>` and the media port range `30000-40000`. `systemctl enable --now ngcp-rtpengine-daemon`.
- [ ] **Step 2: Install Kamailio** (`apt-get install -y kamailio kamailio-tls-modules kamailio-websocket-modules kamailio-json-modules`).
- [ ] **Step 3: Configure Kamailio** `kamailio.cfg`: enable `tls.so`, `websocket.so`, `nathelper`, `rtpengine` modules; load TLS cert for `sip.dotmac.io`; WSS listener on `tls:EDGE_PUBLIC_IP:443`; set the rtpengine socket; route `INVITE`/`REGISTER` to CORE FreeSWITCH (`<CORE_PRIVATE_IP>:5060`) and offer/answer through RTPengine for WebRTC↔SIP and SRTP↔RTP.
- [ ] **Step 4: TLS cert.** Issue a real cert for `sip.dotmac.io` (the WSS endpoint must present a valid cert — Flutter/browser clients reject self-signed). `systemctl enable --now kamailio`.
- [ ] **Step 5: Verify.** `kamcmd core.info` healthy; `rtpengine-ctl list` healthy; Kamailio log shows it forwards a test REGISTER to CORE.

### Task 5: Install coturn on EDGE

**Deliverable:** TURN/STUN reachable and validated.

- [ ] **Step 1: Install** `apt-get install -y coturn`. Enable in `/etc/default/coturn`.
- [ ] **Step 2: Configure** `/etc/turnserver.conf`: `listening-ip=EDGE_PUBLIC_IP`, `realm=dotmac.io`, `use-auth-secret`, a `static-auth-secret` (record it — dotmac_voice will mint TURN creds with it), TLS on `5349`.
- [ ] **Step 3: Start.** `systemctl enable --now coturn`.
- [ ] **Step 4: Verify.** Use the WebRTC `trickle-ice` test page (or `turnutils_uclient`) against `turn:sip.dotmac.io:3478` with a credential derived from the secret — confirm a `relay` candidate is returned.

### Task 6: MILESTONE — one real WebRTC on-net call through the edge

**Deliverable:** A browser WebRTC softphone, coming from the public internet, registers via Kamailio WSS and completes a call between `1001` and `1002` with media relayed by RTPengine. **This is the Tier 0 infra acceptance gate.**

- [ ] **Step 1:** Serve a minimal SIP.js (or JsSIP) test page configured with WSS `wss://sip.dotmac.io:443`, the coturn ICE server, and `1001@test.local` credentials.
- [ ] **Step 2:** From a machine OUTSIDE your network, open the page and register. Confirm registration in `kamcmd ul.dump` and `fs_cli -x 'sofia status profile internal reg'`.
- [ ] **Step 3:** Register `1002` on a second external client (or a LAN phone). Call `1002` from the web `1001`.
- [ ] **Step 4: Verify.** Two-way audio; `rtpengine-ctl list` shows an active session relaying media; `fs_cli -x 'show channels'` shows the bridged call. **Gate: do not proceed until this call works.**

### Task 7: Fraud & exposure baseline

**Deliverable:** The edge is hardened before any token-based client access.

- [ ] **Step 1: Lock the dialplan.** In FreeSWITCH/FusionPBX, ensure no default route allows external/PSTN dialing (no trunk exists yet); on-net extensions only.
- [ ] **Step 2: Kamailio anti-abuse.** Enable `pike` (flood detection) + registration throttling + source ACL (drop SIP scanners); set `max-forwards` and rate limits.
- [ ] **Step 3: fail2ban.** Add jails for FreeSWITCH (`/var/log/freeswitch/freeswitch.log`) and Kamailio auth failures.
- [ ] **Step 4: Verify.** Run `sipvicious`/`svmap` against the edge from a throwaway host — confirm it gets throttled/banned, not a list of extensions. Confirm a registered extension cannot dial any off-net number.

---

# PART 2 — `dotmac_voice` control-plane skeleton (TDD tasks)

> Standard repo: `poetry run pytest tests/ -q`. All work in `~/projects/dotmac_voice`. Each task is test-first.

### Task 8: Voice settings + dev/runtime dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/config.py`
- Modify: `tests/conftest.py` (`MockSettings`)
- Modify: `.env.example`, `docker-compose.yml`
- Test: `tests/test_voice_config.py`

**Interfaces:**
- Produces: `settings.fusionpbx_api_url`, `settings.fusionpbx_api_key`, `settings.esl_host`, `settings.esl_port`, `settings.esl_password`, `settings.edge_wss_url`, `settings.voice_ingress_api_keys` (comma-separated), `settings.voice_ingress_allowed_ips` (comma-separated), `settings.token_signing_key`.

- [ ] **Step 1: Add dependencies.**
```bash
poetry add httpx@^0.27.0 greenswitch@^0.0.9
poetry add --group dev respx@^0.21.1
```

- [ ] **Step 2: Write the failing test.**
```python
# tests/test_voice_config.py
from app.config import settings

def test_voice_settings_present():
    assert settings.fusionpbx_api_url
    assert settings.esl_port == 8021
    assert hasattr(settings, "voice_ingress_api_keys")
    assert hasattr(settings, "token_signing_key")
```

- [ ] **Step 2b: Run it, expect FAIL** (`AttributeError`). Run: `poetry run pytest tests/test_voice_config.py -q`.

- [ ] **Step 3: Add settings** to the `Settings` dataclass body in `app/config.py`:
```python
    fusionpbx_api_url: str = os.getenv("FUSIONPBX_API_URL", "http://localhost:8080")
    fusionpbx_api_key: str = os.getenv("FUSIONPBX_API_KEY", "")
    esl_host: str = os.getenv("ESL_HOST", "localhost")
    esl_port: int = int(os.getenv("ESL_PORT", "8021"))
    esl_password: str = os.getenv("ESL_PASSWORD", "ClueCon")
    edge_wss_url: str = os.getenv("EDGE_WSS_URL", "wss://sip.dotmac.io:443")
    voice_ingress_api_keys: str = os.getenv("VOICE_INGRESS_API_KEYS", "")
    voice_ingress_allowed_ips: str = os.getenv("VOICE_INGRESS_ALLOWED_IPS", "")
    token_signing_key: str = os.getenv("TOKEN_SIGNING_KEY", "dev-token-key")
```

- [ ] **Step 4: Mirror in `MockSettings`** (`tests/conftest.py`):
```python
    fusionpbx_api_url = "http://localhost:8080"
    fusionpbx_api_key = "test-key"
    esl_host = "localhost"
    esl_port = 8021
    esl_password = "ClueCon"
    edge_wss_url = "wss://sip.dotmac.io:443"
    voice_ingress_api_keys = "test-ingress-key"
    voice_ingress_allowed_ips = ""
    token_signing_key = "test-token-key"
```

- [ ] **Step 5: Mirror env** in `.env.example` and each service's `environment:` block in `docker-compose.yml` (`FUSIONPBX_API_URL`, `FUSIONPBX_API_KEY`, `ESL_HOST`, `ESL_PORT`, `ESL_PASSWORD`, `EDGE_WSS_URL`, `VOICE_INGRESS_API_KEYS`, `VOICE_INGRESS_ALLOWED_IPS`, `TOKEN_SIGNING_KEY`).

- [ ] **Step 6: Run test, expect PASS.** `poetry run pytest tests/test_voice_config.py -q`.

- [ ] **Step 7: Commit.**
```bash
git add pyproject.toml poetry.lock app/config.py tests/conftest.py tests/test_voice_config.py .env.example docker-compose.yml
git commit -m "feat(voice): add FusionPBX/ESL/edge settings and deps"
```

### Task 9: `VoiceDomain` + `Extension` models + migration

**Files:**
- Create: `app/models/voice.py`
- Modify: `alembic/env.py`, `tests/conftest.py` (model import)
- Create: `alembic/versions/0xx_voice_domains.py`
- Test: `tests/test_voice_models.py`

**Interfaces:**
- Produces: `VoiceDomain(id, customer_id:str, fusionpbx_domain:str, sync_status:SyncStatus, last_reconciled_at)`, `Extension(id, voice_domain_id, number:str, display_name:str, voicemail_enabled:bool, sync_status:SyncStatus)`, `SyncStatus` enum (`pending|synced|drift|error`).

- [ ] **Step 1: Write the failing test.**
```python
# tests/test_voice_models.py
import uuid
from app.models.voice import VoiceDomain, Extension, SyncStatus

def test_create_voice_domain_with_extension(db_session):
    d = VoiceDomain(customer_id="cust-1", fusionpbx_domain="cust1.voice.local")
    db_session.add(d); db_session.flush()
    assert d.sync_status == SyncStatus.pending
    ext = Extension(voice_domain_id=d.id, number="1001", display_name="Front desk")
    db_session.add(ext); db_session.flush()
    assert ext.sync_status == SyncStatus.pending
    assert ext.voicemail_enabled is True
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`). `poetry run pytest tests/test_voice_models.py -q`.

- [ ] **Step 3: Create the models** (`app/models/voice.py`):
```python
import enum, uuid
from datetime import UTC, datetime
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class SyncStatus(enum.Enum):
    pending = "pending"
    synced = "synced"
    drift = "drift"
    error = "error"

class VoiceDomain(Base):
    __tablename__ = "voice_domains"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, unique=True)
    fusionpbx_domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sync_status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus), default=SyncStatus.pending, nullable=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

class Extension(Base):
    __tablename__ = "voice_extensions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    voice_domain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("voice_domains.id"), nullable=False, index=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    voicemail_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus), default=SyncStatus.pending, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
```

- [ ] **Step 4: Register the module** — add `from app.models import voice  # noqa: F401` to the model-import block in `alembic/env.py` AND to the model imports in `tests/conftest.py` (so `metadata.create_all` builds the tables).

- [ ] **Step 5: Run test, expect PASS.** `poetry run pytest tests/test_voice_models.py -q`.

- [ ] **Step 6: Generate + hand-edit the migration.**
```bash
make migrate-new msg="add voice_domains and voice_extensions"
```
Edit the generated file to the idempotent pattern:
```python
def upgrade() -> None:
    conn = op.get_bind(); inspector = sa.inspect(conn)
    sync_status = postgresql.ENUM("pending","synced","drift","error", name="syncstatus", create_type=False)
    sync_status.create(conn, checkfirst=True)
    if not inspector.has_table("voice_domains"):
        op.create_table("voice_domains",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("customer_id", sa.String(64), nullable=False),
            sa.Column("fusionpbx_domain", sa.String(255), nullable=False),
            sa.Column("sync_status", sa.Enum("pending","synced","drift","error", name="syncstatus", create_type=False), nullable=False),
            sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_voice_domains_customer_id", "voice_domains", ["customer_id"], unique=True)
    if not inspector.has_table("voice_extensions"):
        op.create_table("voice_extensions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("voice_domain_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("voice_domains.id"), nullable=False),
            sa.Column("number", sa.String(32), nullable=False),
            sa.Column("display_name", sa.String(120), nullable=False),
            sa.Column("voicemail_enabled", sa.Boolean(), nullable=False),
            sa.Column("sync_status", sa.Enum("pending","synced","drift","error", name="syncstatus", create_type=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_voice_extensions_voice_domain_id", "voice_extensions", ["voice_domain_id"])

def downgrade() -> None:
    op.drop_table("voice_extensions"); op.drop_table("voice_domains")
    op.execute("DROP TYPE IF EXISTS syncstatus")
```

- [ ] **Step 7: Commit.**
```bash
git add app/models/voice.py alembic/ tests/conftest.py tests/test_voice_models.py
git commit -m "feat(voice): add VoiceDomain and Extension models + migration"
```

### Task 10: FusionPBX REST client

**Files:**
- Create: `app/services/fusionpbx/__init__.py`, `app/services/fusionpbx/client.py`
- Test: `tests/test_fusionpbx_client.py`

**Interfaces:**
- Produces: `FusionpbxClient(base_url, api_key)` with `list_domains() -> list[dict]`, `create_domain(name) -> dict`, `list_extensions(domain) -> list[dict]`, `create_extension(domain, number, password, display_name) -> dict`. Raises `ServiceUnavailableError` on transport error, `BadRequestError` on 4xx.

- [ ] **Step 1: Write the failing test** (mock HTTP with `respx`):
```python
# tests/test_fusionpbx_client.py
import httpx, respx, pytest
from app.services.fusionpbx.client import FusionpbxClient
from app.services.exceptions import ServiceUnavailableError

@respx.mock
def test_list_domains_returns_parsed():
    respx.get("http://fpbx/api/domains").mock(return_value=httpx.Response(200, json={"domains": [{"name": "a.local"}]}))
    c = FusionpbxClient("http://fpbx", "k")
    assert c.list_domains() == [{"name": "a.local"}]

@respx.mock
def test_transport_error_raises_service_unavailable():
    respx.get("http://fpbx/api/domains").mock(side_effect=httpx.ConnectError("down"))
    c = FusionpbxClient("http://fpbx", "k")
    with pytest.raises(ServiceUnavailableError):
        c.list_domains()
```

- [ ] **Step 2: Run, expect FAIL.** `poetry run pytest tests/test_fusionpbx_client.py -q`.

- [ ] **Step 3: Implement the client.**
```python
# app/services/fusionpbx/client.py
import httpx
from app.services.exceptions import BadRequestError, ServiceUnavailableError

class FusionpbxClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)

    def _get(self, path: str) -> dict:
        try:
            r = self._client.get(path)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"FusionPBX unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise BadRequestError(f"FusionPBX {r.status_code}: {r.text}")
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"FusionPBX unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise BadRequestError(f"FusionPBX {r.status_code}: {r.text}")
        return r.json()

    def list_domains(self) -> list[dict]:
        return self._get("/api/domains").get("domains", [])

    def create_domain(self, name: str) -> dict:
        return self._post("/api/domains", {"name": name})

    def list_extensions(self, domain: str) -> list[dict]:
        return self._get(f"/api/domains/{domain}/extensions").get("extensions", [])

    def create_extension(self, domain: str, number: str, password: str, display_name: str = "") -> dict:
        return self._post(f"/api/domains/{domain}/extensions", {"number": number, "password": password, "display_name": display_name})
```

- [ ] **Step 4: Run test, expect PASS.** `poetry run pytest tests/test_fusionpbx_client.py -q`.

- [ ] **Step 5: Commit.**
```bash
git add app/services/fusionpbx/ tests/test_fusionpbx_client.py
git commit -m "feat(voice): add FusionPBX REST client"
```

### Task 11: `reconcile_voice` (desired-vs-actual delta + apply)

**Files:**
- Create: `app/services/reconcile/__init__.py`, `app/services/reconcile/voice.py`
- Test: `tests/test_reconcile_voice.py`

**Interfaces:**
- Consumes: `FusionpbxClient` (Task 10), `VoiceDomain`/`Extension` (Task 9).
- Produces: `compute_delta(desired_numbers: set[str], actual_numbers: set[str]) -> VoiceDelta` (with `.to_create: set[str]`, `.to_delete: set[str]`); `reconcile_voice(db, client, customer_id) -> SyncStatus`.

- [ ] **Step 1: Write the failing test** (pure delta + reconcile with a fake client):
```python
# tests/test_reconcile_voice.py
from app.services.reconcile.voice import compute_delta, reconcile_voice
from app.models.voice import VoiceDomain, Extension, SyncStatus

def test_compute_delta_diffs_sets():
    d = compute_delta({"1001", "1002"}, {"1001"})
    assert d.to_create == {"1002"} and d.to_delete == set()

class _FakeClient:
    def __init__(self): self.created = []
    def list_extensions(self, domain): return [{"number": "1001"}]
    def create_extension(self, domain, number, password, display_name=""): self.created.append(number)

def test_reconcile_creates_missing_extension(db_session):
    dom = VoiceDomain(customer_id="c1", fusionpbx_domain="c1.local"); db_session.add(dom); db_session.flush()
    db_session.add_all([
        Extension(voice_domain_id=dom.id, number="1001"),
        Extension(voice_domain_id=dom.id, number="1002"),
    ]); db_session.flush()
    client = _FakeClient()
    status = reconcile_voice(db_session, client, "c1")
    assert "1002" in client.created
    assert status == SyncStatus.synced
    assert dom.sync_status == SyncStatus.synced

def test_reconcile_detects_extra_actual_extension(db_session):
    dom = VoiceDomain(customer_id="c2", fusionpbx_domain="c2.local"); db_session.add(dom); db_session.flush()
    db_session.add(Extension(voice_domain_id=dom.id, number="1001")); db_session.flush()
    client = _FakeClient()
    client.list_extensions = lambda domain: [{"number": "1001"}, {"number": "9999"}]
    status = reconcile_voice(db_session, client, "c2")
    assert status in {SyncStatus.drift, SyncStatus.synced}
    # If Tier 0 chooses delete-on-drift, also assert the fake client recorded a delete for "9999".
```

- [ ] **Step 2: Run, expect FAIL.** `poetry run pytest tests/test_reconcile_voice.py -q`.

- [ ] **Step 3: Implement.**
```python
# app/services/reconcile/voice.py
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.voice import VoiceDomain, Extension, SyncStatus
from app.services.exceptions import NotFoundError, ServiceUnavailableError

@dataclass(frozen=True)
class VoiceDelta:
    to_create: set[str]
    to_delete: set[str]

def compute_delta(desired_numbers: set[str], actual_numbers: set[str]) -> VoiceDelta:
    return VoiceDelta(to_create=desired_numbers - actual_numbers, to_delete=actual_numbers - desired_numbers)

def reconcile_voice(db: Session, client, customer_id: str) -> SyncStatus:
    domain = db.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == customer_id))
    if not domain:
        raise NotFoundError(f"No voice domain for customer {customer_id}")
    desired = {e.number for e in db.scalars(select(Extension).where(Extension.voice_domain_id == domain.id))}
    try:
        actual = {e["number"] for e in client.list_extensions(domain.fusionpbx_domain)}
        delta = compute_delta(desired, actual)
        for number in sorted(delta.to_create):
            client.create_extension(domain.fusionpbx_domain, number, password="", display_name="")
        if delta.to_delete:
            # Tier 0 policy decision:
            # - preferred for authoritative desired state: delete extras via FusionPBX and then mark synced
            # - conservative fallback: mark drift and leave extras untouched
            domain.sync_status = SyncStatus.drift
        else:
            domain.sync_status = SyncStatus.synced
    except ServiceUnavailableError:
        domain.sync_status = SyncStatus.error
    domain.last_reconciled_at = datetime.now(UTC)
    db.flush()
    return domain.sync_status
```

- [ ] **Step 4: Run test, expect PASS.** `poetry run pytest tests/test_reconcile_voice.py -q`.

- [ ] **Step 5: Commit.**
```bash
git add app/services/reconcile/ tests/test_reconcile_voice.py
git commit -m "feat(voice): add reconcile_voice delta + apply"
```

### Task 12: Authenticated ingress dependency (API-key + IP allowlist)

**Files:**
- Create: `app/services/ingress_auth.py`
- Test: `tests/test_ingress_auth.py`

**Interfaces:**
- Produces: `require_ingress` FastAPI dependency — 401 if `X-API-Key` is missing or does not constant-time match a configured key; 403 if `settings.voice_ingress_allowed_ips` is non-empty and the trusted-proxy-aware client IP is not in it.
- Production deployment requirement: front the API with an mTLS-capable reverse proxy and edge rate limiting. The app dependency is an in-process gate, not the whole perimeter.

- [ ] **Step 1: Write the failing test** (a throwaway router using the dep, exercised via `client`):
```python
# tests/test_ingress_auth.py
from fastapi import Depends
from app.services.ingress_auth import require_ingress

def _mount(app):
    from fastapi import APIRouter
    r = APIRouter()
    @r.get("/_ingress_ping")
    def ping(_=Depends(require_ingress)): return {"ok": True}
    app.include_router(r)

def test_missing_key_401(client):
    _mount(client.app)
    assert client.get("/_ingress_ping").status_code == 401

def test_valid_key_200(client):
    _mount(client.app)
    assert client.get("/_ingress_ping", headers={"X-API-Key": "test-ingress-key"}).status_code == 200
```

- [ ] **Step 2: Run, expect FAIL.** `poetry run pytest tests/test_ingress_auth.py -q`.

- [ ] **Step 3: Implement.**
```python
# app/services/ingress_auth.py
import secrets
from fastapi import Header, HTTPException, Request, status
from app.config import settings
from app.middleware.rate_limit import _get_client_ip

def _csv(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())

def _allowed_api_key(candidate: str, allowed_keys: tuple[str, ...]) -> bool:
    return any(secrets.compare_digest(candidate, allowed) for allowed in allowed_keys)

def require_ingress(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    allowed_keys = _csv(settings.voice_ingress_api_keys)
    if not x_api_key or not _allowed_api_key(x_api_key, allowed_keys):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    allowed_ips = _csv(settings.voice_ingress_allowed_ips)
    if allowed_ips:
        client_ip = _get_client_ip(request)
        if client_ip not in allowed_ips:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ip not allowed")
```

- [ ] **Step 4: Run test, expect PASS.** `poetry run pytest tests/test_ingress_auth.py -q`.

- [ ] **Step 5: Commit.**
```bash
git add app/services/ingress_auth.py tests/test_ingress_auth.py
git commit -m "feat(voice): add API-key + IP-allowlist ingress dependency"
```

### Task 13: Provisioning-intent API endpoint

**Files:**
- Create: `app/schemas/voice.py`, `app/api/provisioning.py`
- Modify: `app/main.py` (register router)
- Test: `tests/test_api_provisioning.py`

**Interfaces:**
- Consumes: `require_ingress` (Task 12), `reconcile_voice` (Task 11), `FusionpbxClient` (Task 10), models (Task 9).
- Produces: `PUT /provisioning/domains/{customer_id}` body `{fusionpbx_domain: str, extensions: [{number, display_name?}]}` → replaces desired `VoiceDomain`+`Extension` state for that customer, runs `reconcile_voice`, returns `{customer_id, sync_status}`.
- Desired-state rule: the payload is authoritative for the domain. Update existing extension attributes, add missing desired extensions, and mark/remove local desired extensions absent from the payload before reconciling FusionPBX.

- [ ] **Step 1: Write the failing test** (override the client dependency with a fake; ingress key in headers):
```python
# tests/test_api_provisioning.py
INGRESS = {"X-API-Key": "test-ingress-key"}

class _FakeClient:
    def list_extensions(self, domain): return []
    def create_extension(self, domain, number, password, display_name=""): pass

def test_put_provisioning_creates_and_syncs(client):
    from app.api.provisioning import get_fusionpbx_client
    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _FakeClient()
    body = {"fusionpbx_domain": "c1.local", "extensions": [{"number": "1001"}, {"number": "1002"}]}
    r = client.put("/provisioning/domains/c1", json=body, headers=INGRESS)
    assert r.status_code == 200
    assert r.json()["sync_status"] == "synced"
    client.app.dependency_overrides.pop(get_fusionpbx_client)

def test_put_provisioning_requires_key(client):
    r = client.put("/provisioning/domains/c1", json={"fusionpbx_domain": "c1.local", "extensions": []})
    assert r.status_code == 401

def test_put_provisioning_replaces_desired_extensions(client):
    from app.api.provisioning import get_fusionpbx_client
    client.app.dependency_overrides[get_fusionpbx_client] = lambda: _FakeClient()
    first = {"fusionpbx_domain": "c2.local", "extensions": [{"number": "1001"}, {"number": "1002"}]}
    second = {"fusionpbx_domain": "c2.local", "extensions": [{"number": "1002", "display_name": "Support"}]}
    assert client.put("/provisioning/domains/c2", json=first, headers=INGRESS).status_code == 200
    assert client.put("/provisioning/domains/c2", json=second, headers=INGRESS).status_code == 200
    # Assert the local desired-state table now has only 1002 and its display_name changed.
    client.app.dependency_overrides.pop(get_fusionpbx_client)
```

- [ ] **Step 2: Run, expect FAIL.** `poetry run pytest tests/test_api_provisioning.py -q`.

- [ ] **Step 3: Add schemas** (`app/schemas/voice.py`):
```python
from pydantic import BaseModel, ConfigDict, Field

class ExtensionIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    number: str = Field(min_length=1, max_length=32)
    display_name: str = ""

class DomainIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    fusionpbx_domain: str = Field(min_length=1, max_length=255)
    extensions: list[ExtensionIntent] = []

class DomainSyncResult(BaseModel):
    customer_id: str
    sync_status: str
```

- [ ] **Step 4: Implement the route** (`app/api/provisioning.py`):
```python
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.config import settings
from app.models.voice import VoiceDomain, Extension
from app.schemas.voice import DomainIntent, DomainSyncResult
from app.services.fusionpbx.client import FusionpbxClient
from app.services.ingress_auth import require_ingress
from app.services.reconcile.voice import reconcile_voice

router = APIRouter(prefix="/provisioning", tags=["provisioning"], dependencies=[Depends(require_ingress)])

def get_fusionpbx_client() -> FusionpbxClient:
    return FusionpbxClient(settings.fusionpbx_api_url, settings.fusionpbx_api_key)

def _commit(db: Session) -> None:
    db.commit()

@router.put("/domains/{customer_id}", response_model=DomainSyncResult)
def put_domain(customer_id: str, payload: DomainIntent, db: Session = Depends(get_db), client: FusionpbxClient = Depends(get_fusionpbx_client)):
    domain = db.scalar(select(VoiceDomain).where(VoiceDomain.customer_id == customer_id))
    if not domain:
        domain = VoiceDomain(customer_id=customer_id, fusionpbx_domain=payload.fusionpbx_domain)
        db.add(domain); db.flush()
    existing = {e.number: e for e in db.scalars(select(Extension).where(Extension.voice_domain_id == domain.id))}
    desired_numbers = {ext.number for ext in payload.extensions}
    for number, row in existing.items():
        if number not in desired_numbers:
            db.delete(row)
    for ext in payload.extensions:
        if ext.number in existing:
            existing[ext.number].display_name = ext.display_name
        else:
            db.add(Extension(voice_domain_id=domain.id, number=ext.number, display_name=ext.display_name))
    db.flush()
    status = reconcile_voice(db, client, customer_id)
    _commit(db)
    return DomainSyncResult(customer_id=customer_id, sync_status=status.value)
```

- [ ] **Step 5: Register** in `app/main.py`:
```python
from app.api.provisioning import router as provisioning_router
_include_api_router(provisioning_router)
```
(No `require_role` — the router already guards with `require_ingress`.)

- [ ] **Step 6: Run test, expect PASS.** `poetry run pytest tests/test_api_provisioning.py -q`.

- [ ] **Step 7: Commit.**
```bash
git add app/schemas/voice.py app/api/provisioning.py app/main.py tests/test_api_provisioning.py
git commit -m "feat(voice): add provisioning-intent endpoint (reconcile_voice)"
```

### Task 14: Ephemeral SIP/WebRTC token minting

**Files:**
- Create: `app/services/tokens.py`, `app/api/tokens.py`
- Modify: `app/main.py` (register router)
- Test: `tests/test_tokens.py`

**Interfaces:**
- Consumes: `require_ingress` (Task 12), `settings.token_signing_key`, `settings.edge_wss_url`.
- Produces: `mint_token(subject, scope, ttl_seconds) -> dict{token, sip_uri, wss_endpoint, expires_in}` (HS256 JWT with `sub`, `scope`, `exp`); `POST /tokens` body `{subject, scope, ttl_seconds?}`.
- Tier 0 token policy: TTL must be bounded (`1..300` seconds), subject/scope lengths are bounded, and `TOKEN_SIGNING_KEY=dev-token-key` is a production startup failure. Persistent token grants/revocation are documented as Tier 1 unless Tier 0 needs active revocation before any public client release.

- [ ] **Step 1: Write the failing test.**
```python
# tests/test_tokens.py
import pytest
from jose import jwt
from app.services.tokens import mint_token
from app.config import settings
INGRESS = {"X-API-Key": "test-ingress-key"}

def test_mint_token_encodes_scope_and_exp():
    out = mint_token("subscriber-1", "queue:support", 60)
    claims = jwt.decode(out["token"], settings.token_signing_key, algorithms=["HS256"])
    assert claims["sub"] == "subscriber-1" and claims["scope"] == "queue:support"
    assert out["wss_endpoint"] == settings.edge_wss_url and out["expires_in"] == 60

def test_mint_token_rejects_invalid_ttl():
    with pytest.raises(ValueError):
        mint_token("subscriber-1", "queue:support", 99999)

def test_tokens_endpoint_requires_key(client):
    assert client.post("/tokens", json={"subject": "s1", "scope": "queue:support"}).status_code == 401

def test_tokens_endpoint_mints(client):
    r = client.post("/tokens", json={"subject": "s1", "scope": "queue:support"}, headers=INGRESS)
    assert r.status_code == 201 and r.json()["scope"] == "queue:support"

def test_tokens_endpoint_rejects_excessive_ttl(client):
    r = client.post("/tokens", json={"subject": "s1", "scope": "queue:support", "ttl_seconds": 99999}, headers=INGRESS)
    assert r.status_code == 422
```

- [ ] **Step 2: Run, expect FAIL.** `poetry run pytest tests/test_tokens.py -q`.

- [ ] **Step 3: Implement the service** (`app/services/tokens.py`):
```python
from datetime import UTC, datetime, timedelta
from jose import jwt
from app.config import settings

MAX_TOKEN_TTL_SECONDS = 300

def mint_token(subject: str, scope: str, ttl_seconds: int = 60) -> dict:
    if ttl_seconds < 1 or ttl_seconds > MAX_TOKEN_TTL_SECONDS:
        raise ValueError("ttl_seconds must be between 1 and 300")
    now = datetime.now(UTC)
    claims = {"sub": subject, "scope": scope, "iat": int(now.timestamp()), "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp())}
    token = jwt.encode(claims, settings.token_signing_key, algorithm="HS256")
    return {"token": token, "sip_uri": f"sip:{subject}@dotmac.io", "wss_endpoint": settings.edge_wss_url, "expires_in": ttl_seconds, "scope": scope}
```

- [ ] **Step 4: Implement the route** (`app/api/tokens.py`):
```python
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from app.services import tokens as token_service
from app.services.ingress_auth import require_ingress

router = APIRouter(prefix="/tokens", tags=["tokens"], dependencies=[Depends(require_ingress)])

class TokenRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    scope: str = Field(min_length=1, max_length=120)
    ttl_seconds: int = Field(default=60, gt=0, le=300)

@router.post("", status_code=status.HTTP_201_CREATED)
def create_token(payload: TokenRequest):
    return token_service.mint_token(payload.subject, payload.scope, payload.ttl_seconds)
```

- [ ] **Step 5: Register** in `app/main.py`:
```python
from app.api.tokens import router as tokens_router
_include_api_router(tokens_router)
```

- [ ] **Step 6: Run test, expect PASS.** `poetry run pytest tests/test_tokens.py -q`.

- [ ] **Step 7: Commit.**
```bash
git add app/services/tokens.py app/api/tokens.py app/main.py tests/test_tokens.py
git commit -m "feat(voice): add ephemeral scoped token minting endpoint"
```

### Task 15: ESL bridge skeleton (event normalization)

**Files:**
- Create: `app/services/freeswitch/__init__.py`, `app/services/freeswitch/esl.py`
- Test: `tests/test_esl.py`

**Interfaces:**
- Produces: `normalize_event(raw: dict) -> CallEvent | None` (pure; maps FreeSWITCH event headers → `CallEvent(call_uuid, name, direction, caller, callee, subscriber_id)`); `EslBridge(host, port, password)` with `connect()` and `on_event(callback)` (thin wrapper over `greenswitch.InboundESL`, not exercised live in unit tests).

- [ ] **Step 1: Write the failing test** (pure normalization — no live ESL):
```python
# tests/test_esl.py
from app.services.freeswitch.esl import normalize_event

def test_normalize_channel_answer():
    raw = {"Event-Name": "CHANNEL_ANSWER", "Unique-ID": "abc-123",
           "Call-Direction": "inbound", "Caller-Caller-ID-Number": "2348012345678",
           "Caller-Destination-Number": "support",
           "variable_dotmac_subscriber_id": "subscriber-9"}
    ev = normalize_event(raw)
    assert ev.call_uuid == "abc-123" and ev.name == "CHANNEL_ANSWER"
    assert ev.direction == "inbound" and ev.subscriber_id == "subscriber-9"

def test_normalize_ignores_unknown_event():
    assert normalize_event({"Event-Name": "RE_SCHEDULE"}) is None
```

- [ ] **Step 2: Run, expect FAIL.** `poetry run pytest tests/test_esl.py -q`.

- [ ] **Step 3: Implement.**
```python
# app/services/freeswitch/esl.py
from dataclasses import dataclass

_RELEVANT = {"CHANNEL_CREATE", "CHANNEL_ANSWER", "CHANNEL_HANGUP", "CHANNEL_HANGUP_COMPLETE"}

@dataclass(frozen=True)
class CallEvent:
    call_uuid: str
    name: str
    direction: str
    caller: str
    callee: str
    subscriber_id: str | None

def normalize_event(raw: dict) -> CallEvent | None:
    name = raw.get("Event-Name", "")
    if name not in _RELEVANT:
        return None
    return CallEvent(
        call_uuid=raw.get("Unique-ID", ""),
        name=name,
        direction=raw.get("Call-Direction", ""),
        caller=raw.get("Caller-Caller-ID-Number", ""),
        callee=raw.get("Caller-Destination-Number", ""),
        subscriber_id=raw.get("variable_dotmac_subscriber_id") or None,
    )

class EslBridge:
    def __init__(self, host: str, port: int, password: str) -> None:
        self._host, self._port, self._password = host, port, password
        self._conn = None
        self._callback = None

    def on_event(self, callback) -> None:
        self._callback = callback

    def connect(self) -> None:  # pragma: no cover - exercised in integration, not unit tests
        import greenswitch
        self._conn = greenswitch.InboundESL(host=self._host, port=self._port, password=self._password)
        self._conn.connect()
        self._conn.register_handle("*", self._dispatch)
        self._conn.send("events plain ALL")

    def _dispatch(self, event) -> None:  # pragma: no cover
        normalized = normalize_event(dict(event.headers))
        if normalized and self._callback:
            self._callback(normalized)
```

- [ ] **Step 4: Run test, expect PASS.** `poetry run pytest tests/test_esl.py -q`.

- [ ] **Step 5: Run the full suite** to confirm no regressions: `poetry run pytest tests/ -q`.

- [ ] **Step 6: Commit.**
```bash
git add app/services/freeswitch/ tests/test_esl.py
git commit -m "feat(voice): add ESL bridge skeleton with event normalization"
```

---

## Tier 0 Acceptance

- **Infra:** Task 6 milestone call works (external WebRTC ↔ FreeSWITCH via Kamailio/RTPengine); Task 7 abuse scan is throttled.
- **App:** `poetry run pytest tests/ -q` green; `PUT /provisioning/domains/{customer_id}` reconciles to FusionPBX behind API-key+IP ingress; `POST /tokens` mints a scoped JWT; ESL events normalize. dotmac_voice runs locally (`make docker-up`) with ESL/FusionPBX env pointed at CORE.

## Out of scope for Tier 0 (later tiers)
- CDR ingest + rating feed, outbound webhooks to crm, click-to-dial (Tier 2).
- sub `reconcile_voice` caller + selfcare Phone tab (Tier 1).
- crm voice channel + softphone, talk-to-agent client (Tier 2).
- PSTN trunk/DID + per-minute fraud limits (PSTN go-live; not required for the on-net Tier 0 acceptance gate).

## Open dependencies (from spec §10)
- Carrier SIP trunk / DID provider (required for PSTN go-live only; not required for the on-net Tier 0 acceptance gate).
- Public-IP/DMZ provisioning at the local site (Task 1).
- `voice.dotmac.io` reverse-proxy + cert for the API ingress (deploy alongside Task 4's edge).
