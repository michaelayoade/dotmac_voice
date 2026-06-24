# Voice Runtime Durability — Implementation Plan

> Implement task-by-task with TDD. Builds on the lifecycle + multi-tenant work.

**Goal:** Make the live runtime survive restarts and concurrency — re-apply in-memory
queue state from the persistent DB, serialize concurrent reconciles, and expose
read APIs so callers can see live state.

**Problem:** `ensure_queue` issues `callcenter_config` runtime commands only when the DB
row *changes*. After a FreeSWITCH restart the DB is unchanged but mod_callcenter's
in-memory queues/agents/tiers are gone — a plain reconcile won't restore them. Also,
two concurrent reconciles for one customer can race, and there's no way to read live
state (call history, etc.).

**Tech:** same stack — SQLAlchemy Core client (SQLite-injectable tests), pytest, require_ingress.

## Global Constraints
- Idempotent everywhere; resync is safe to run repeatedly (mod_callcenter tolerates re-load/re-add).
- ESL stays bound to 127.0.0.1 (runtime commands go through the injected `commander`).
- No new external network integrations in this cluster (registration-status via Kamailio is deferred).

---

### Task R1: Queue runtime resync
**Files:** `app/services/fusionpbx/client.py` (+ `resync_queues(domain_name) -> dict`: read
v_call_center_queues/agents/tiers for the domain and re-issue `queue load` / `agent add` /
`agent set contact` / `agent set status` / `tier add` over `self._commander`, unconditionally);
`app/api/provisioning.py` (+ `POST /domains/{cid}/resync` = reconcile + resync_queues; +
`POST /resync-all` = same for every domain); tests in `test_fusionpbx_client.py`, `test_api_provisioning.py`.
- [ ] TDD: provision a queue, then `resync_queues` re-issues `queue load`/`tier add` from the DB
  (commander records them) even with no DB change; endpoint returns counts; resync-all hits all domains.

### Task R2: Reconcile concurrency lock
**Files:** `app/services/reconcile/voice.py` (domain fetch → `.with_for_update()`); test that reconcile
still succeeds (lock is a transparent Postgres row lock; SQLite ignores it).
- [ ] TDD: existing reconcile tests still pass with the lock; add a note-test asserting reconcile
  completes for a domain (lock doesn't break the happy path).

### Task R3: Call-history read API
**Files:** check `app/api/cdr.py`; add `GET /provisioning/domains/{cid}/cdrs` (Cdr rows by customer_id,
most-recent first, limit) if not already exposed; `app/schemas/voice.py` (CdrOut); test.
- [ ] TDD: ingest a Cdr for a customer, GET returns it; unknown customer -> empty list.

## Self-review
Covers: FS-restart runtime recovery (R1), concurrency safety (R2), live read access (R3).
Deferred (noted, need decisions/infra): registration-status + voicemail-message retrieval
(Kamailio query path / v_voicemail_messages schema), automatic restart hook.
