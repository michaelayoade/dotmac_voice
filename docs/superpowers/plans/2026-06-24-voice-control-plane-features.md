# Voice Control-Plane Feature Extension — Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extend the `dotmac_voice` control plane so every PBX capability hand-built this session
(dial-string unlock, voicemail, recording, ring groups, conferences, IVR, queues) is provisioned
through idempotent `FusionpbxClient` methods + `reconcile_voice` + API endpoints — never raw SQL/ESL.

**Architecture:** `FusionpbxClient` (SQLAlchemy Core over the FusionPBX DB, injectable engine for
tests, best-effort `_reload()` via ESL) gains per-feature `ensure_*`/`create_*` methods that write the
same `v_*` rows / dialplan XML I built by hand. Desired state lives in new `app/models/voice.py`
tables; `reconcile_voice` diffs desired→actual and applies. API in `app/api/provisioning.py`
(+ feature routers), `require_ingress` auth. Footguns (dial-string format, `${...}` escaping, colon
cache key, queue naming, callcenter runtime) are hidden inside the client.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 Core+ORM, Postgres (FusionPBX) + app Postgres,
Alembic, pytest (SQLite in-memory, injected client engine + no-op reloader).

## Global Constraints
- FusionPBX domain `voicetest.dotmac`; agent/feature dialplans live in the **public** context, gated
  by `${network_addr} == 10.10.10.1` (Kamailio) — copy verbatim from the committed
  `deploy/core/freeswitch/kamailio-*.xml`.
- All `v_*` writes MUST be **idempotent** (return existing on conflict; `_reload()` only on change).
- Dialplan XML loaded into the FusionPBX DB column verbatim (the client builds the string in Python —
  no shell/heredoc). Cache invalidation is the client's job (`_reload()` → `reloadxml`).
- Dial-string unlock value (verbatim):
  `{sip_invite_domain=${domain_name},sip_h_X-Voice-Domain=${domain_name}}sofia/external/${dialed_user}@10.10.10.1:5060`
- Tests: inject a SQLite engine into `FusionpbxClient` (declare new tables in its metadata), no-op
  reloader; `poetry run pytest tests/ -q`. Migrations: `make migrate-new msg="..."`.
- Known caveat to encode as a docstring + `SyncStatus.drift`, not silently: queue caller↔agent media
  is one-way (mod_callcenter bridge re-anchor) — provisioning works, media is a tracked follow-up.

---

### Task 1: Extension dial-string unlock (foundational)

**Files:** Modify `app/services/fusionpbx/client.py` (v_extensions table decl + `create_extension`);
Test `tests/services/test_fusionpbx_dialstring.py`.

**Interfaces:**
- Produces: `create_extension(...)` now also writes `dial_string` (the unlock constant) on insert.
- Add module constant `DIAL_STRING_UNLOCK: str`.

- [ ] Step 1: failing test — create_extension writes the unlock dial_string to v_extensions.
- [ ] Step 2: run, expect FAIL (column/value missing).
- [ ] Step 3: add `dial_string: String` to the `v_extensions` Core table; add `DIAL_STRING_UNLOCK`
  constant; set `dial_string=DIAL_STRING_UNLOCK` in the insert.
- [ ] Step 4: run, expect PASS. Step 5: commit.

### Task 2: Voicemail provisioning

**Files:** Modify `client.py` (declare `v_voicemails`; add `ensure_voicemail(domain_name, number,
enabled, password) -> dict`); `app/services/reconcile/voice.py` (call ensure_voicemail per extension
where `Extension.voicemail_enabled`); Test `tests/services/test_fusionpbx_voicemail.py`,
`tests/services/test_reconcile_voicemail.py`.

**Interfaces:**
- Produces: `ensure_voicemail(domain_name, number, *, enabled=True, password="") -> {"voicemail_id","created"}`.
- Consumes: `Extension.voicemail_enabled` (existing model field).

- [ ] Steps: failing test (ensure_voicemail inserts v_voicemails row, idempotent) → declare table +
  method (mirror `deploy/core/freeswitch/dialstring-unlock-and-1003.sql` voicemail insert) → pass →
  reconcile test (voicemail_enabled ext gets a box) → wire into reconcile_voice → pass → commit.
- [ ] Note: document the `switch/voicemail/dir` env requirement (Task 8 ensures it).

### Task 3: Recording policy (domain-level)

**Files:** Modify `app/models/voice.py` (`VoiceDomain.recording_enabled: bool = False`);
`client.py` (routing-dialplan generation honors recording — see Task 9; here add the column +
schema/endpoint plumbing); migration; Test `tests/services/test_recording_policy.py`.

**Interfaces:**
- Produces: `VoiceDomain.recording_enabled`; routing dialplan (Task 9) adds the
  `execute_on_answer=record_session ...` actions when true (verbatim from committed
  `kamailio-internal-to-domain.xml`).

- [ ] Steps: failing test (domain with recording_enabled → routing XML contains `record_session`) →
  add column + migration → thread into Task 9 generator → pass → commit.

### Task 4: Conference rooms

**Files:** `app/models/voice.py` (`ConferenceRoom`: id, voice_domain_id FK, number, enabled);
`app/schemas/voice.py` (ConferenceIntent); `client.py` (`ensure_conference(domain_name, number)` →
v_dialplans public XML from committed `kamailio-conference.xml`); `reconcile/voice.py`; migration;
`app/api/provisioning.py` (nested under DomainIntent or a `/conferences` router); Tests.

**Interfaces:** `ensure_conference(domain_name, number) -> {"dialplan_uuid","created"}`.

- [ ] Steps: failing test (ensure_conference inserts a public v_dialplans row whose XML calls
  `conference(${destination_number}@default)`, idempotent) → declare `v_dialplans` Core table + method
  building the XML string verbatim → pass → model + reconcile + endpoint + tests → commit.

### Task 5: Ring groups

**Files:** `app/models/voice.py` (`RingGroup`: number, strategy, timeout; `RingGroupMember`:
ring_group_id, extension_number); schemas; `client.py` (`ensure_ring_group(domain_name, number,
members, strategy, timeout)` → v_dialplans XML bridging `user/<m>@${domain}` joined by `,`
(simultaneous) or `|` (sequential), from committed `kamailio-ringgroup.xml`); reconcile; migration;
router; Tests.

**Interfaces:** `ensure_ring_group(domain_name, number, members: list[str], *, strategy="simultaneous",
timeout=30) -> {"dialplan_uuid","created"}`.

- [ ] Steps: failing test (XML bridges the members via user/ with the right separator + vm fallback)
  → method → pass → model+members+reconcile+endpoint+tests → commit.

### Task 6: IVR menus

**Files:** `app/models/voice.py` (`IvrMenu`: number, greeting_sound, timeout, invalid_action;
`IvrOption`: ivr_menu_id, digit, target_number); schemas; `client.py` (`ensure_ivr(domain_name,
number, greeting, options: dict[str,str], timeout)` → v_dialplans XML with `play_and_get_digits` +
`cond()` routing → `transfer ... XML public`, from committed `kamailio-ivr.xml`); reconcile;
migration; router; Tests.

**Interfaces:** `ensure_ivr(domain_name, number, *, greeting, options, timeout=6000) -> {...}`.

- [ ] Steps: failing test (XML contains play_and_get_digits with the option regex + transfers) →
  method → pass → model+options+reconcile+endpoint+tests → commit.

### Task 7: Call-center queues

**Files:** `app/models/voice.py` (`Queue`: number, name, strategy, moh; `QueueAgent`: queue_id,
extension_number); schemas; `client.py` (declare `v_call_center_queues/agents/tiers`;
`ensure_queue(domain_name, number, name, agents, strategy)` → DB rows **with `queue_name=<number>`**
+ public v_dialplans `callcenter(<number>@<domain>)` + runtime `callcenter_config` ESL via the
reloader/ESL helper, from committed `queue-setup.sql`/`kamailio-queue.xml`); reconcile; migration;
router; Tests.

**Interfaces:** `ensure_queue(domain_name, number, *, name, agents: list[str], strategy="ring-all") -> {...}`.

- [ ] Steps: failing test (rows in the three tables with queue_name=number, agents as callback w/
  `user/<ext>` contact, tiers link them; public dialplan calls callcenter) → method → pass →
  model+agents+reconcile+endpoint+tests → commit.
- [ ] Docstring MUST note the one-way-media follow-up (mod_callcenter bridge); reconcile marks the
  domain `drift` if a queue exists until media is fixed (so it's visible, not silent).

### Task 8: Environment bootstrap

**Files:** `client.py` (`ensure_switch_settings()` → set `v_default_settings switch/voicemail/dir`
verbatim; `modules_present(names) -> dict` read-only check via ESL `module_exists`);
`app/api/provisioning.py` (`POST /provisioning/bootstrap`); Tests.

**Interfaces:** `ensure_switch_settings() -> {"changed": bool}`; `bootstrap` endpoint returns a report.

- [ ] Steps: failing test (ensure_switch_settings updates the dir default-setting idempotently) →
  declare `v_default_settings` Core table + method → pass → endpoint + module-presence report →
  commit. (mod_voicemail/mod_callcenter live in `modules.conf.xml` per deploy README — report only.)

### Task 9: Routing dialplan generation (`ensure_routing`)

**Files:** `client.py` (`ensure_routing(domain_name, *, ext_pattern="1\\d{3}", recording=False)` →
the `kamailio-internal-to-domain` public v_dialplans XML verbatim from the committed file, recording
actions included when `recording=True`); reconcile (call once per domain); Tests.

**Interfaces:** `ensure_routing(domain_name, *, recording=False) -> {"dialplan_uuid","created"}`.

- [ ] Steps: failing test (XML = the committed routing dialplan; recording flag toggles the
  record_session lines) → method (read the committed XML as the template, parametrize) → pass →
  reconcile wires it (honoring `VoiceDomain.recording_enabled` from Task 3) → commit.

### Task 10: `reconcile_voice` full orchestration

**Files:** Modify `app/services/reconcile/voice.py` (after extension reconcile, call `ensure_routing`,
`ensure_voicemail` per ext, then reconcile ring groups / conferences / IVRs / queues from the new
models; set `SyncStatus.drift` if a queue exists; `synced` otherwise); Tests
`tests/services/test_reconcile_voice_full.py`.

**Interfaces:** `reconcile_voice(db, client, customer_id) -> SyncStatus` (unchanged signature; broader
behavior).

- [ ] Steps: failing test (a domain with extensions+voicemail+ring group+ivr → all applied to the
  injected FusionPBX SQLite; status synced; with a queue → drift) → extend reconcile_voice → pass →
  commit. Then a provisioning API integration test (PUT domain with features → 200 + applied).

---

## Self-review
- **Coverage vs the scope matrix:** dial-string (T1), voicemail (T2), recording (T3), conference (T4),
  ring group (T5), IVR (T6), queue (T7), bootstrap/modules+settings (T8), routing dialplan (T9),
  reconcile-all + API (T10). FS-in-path Kamailio config stays infra (`deploy/`) — out of scope by
  design. **No gaps.**
- **Type consistency:** every `ensure_*`/`create_*` returns a dict with `created: bool`; reconcile
  treats `created` as the change signal; models all carry `voice_domain_id` FK + `number`.
- **No placeholders:** each task names the committed `deploy/core/freeswitch/*` file that is the
  verbatim XML/SQL source, so the implementer copies real content, not "TODO".
