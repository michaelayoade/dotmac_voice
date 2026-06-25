# Voice Product Gaps — Implementation Plan

> Implement task-by-task with TDD. From the 10-gap review. Codeable cluster only;
> #1 (media e2e), #2 (queue media), #6 (PSTN/DID) need live infra/carrier and are tracked separately.
> #10 (TestClient hang) does not reproduce here — TestClient API tests pass.

**Order:** G3 (recording) → G7 (CDR lifecycle) → G9 (declarative features) → G4 (bootstrap readiness)
→ G5 (client credential bootstrap) → G8 (webhook retry sweep).

**Tech:** FastAPI + SQLAlchemy 2, alembic, pytest (SQLite + fakes), require_ingress.

## Global Constraints
- Idempotent everywhere; reconcile stays the single apply path.
- ESL bound to 127.0.0.1; runtime commands via the injected commander.
- No new public network services without explicit sign-off.

---

### G3: Recording policy wired end-to-end (#3)
**Files:** `app/models/voice.py` (`VoiceDomain.recording_enabled: bool = False`); migration 012;
`app/schemas/voice.py` (`DomainIntent.recording_enabled`); `app/api/provisioning.py` (set it in
put_domain); `app/services/reconcile/voice.py` (pass `recording=domain.recording_enabled` to
`ensure_routing`); CDR `recording_url` population folded into G7. Tests.
- [ ] TDD: PUT with recording_enabled=true -> domain flag set; reconcile calls ensure_routing(recording=True).

### G7: CDR billing lifecycle (#7)
**Files:** `app/services/cdr/ingest.py` (idempotent upsert by `call_uuid`; populate `recording_url`
from the payload's recording variable); rating-state transitions raw->rated->fed already enum'd —
add a `mark_rated`/`mark_fed` path + a billing-export query; `app/api/cdr.py` (export-marking
endpoint). Tests.
- [ ] TDD: re-ingesting the same call_uuid updates, not duplicates; recording_url populated; rating
  transition endpoint marks rows.

### G9: Declarative feature replace (#9)
**Files:** `app/schemas/voice.py` (extend `DomainIntent` with optional `conferences/ring_groups/
ivrs/queues` lists) OR a new `PUT /provisioning/domains/{cid}/features` taking the full desired set;
`app/api/...` replaces the model set (add/update/delete) then reconcile. Tests.
- [ ] TDD: PUT full feature set replaces existing (adds new, removes absent) in one call.

### G4: Bootstrap readiness endpoint (#4)
**Files:** `app/services/fusionpbx/client.py` (`check_readiness() -> dict`: ESL `module_exists`
voicemail/callcenter + `status`); `app/api/provisioning.py` (`GET /provisioning/bootstrap`). Tests
with fake commander.
- [ ] TDD: readiness reports per-module ok/missing; endpoint returns the report.

### G5: WebRTC client credential bootstrap (#5)
**Files:** `app/api/tokens.py` (expand to return SIP username/domain/auth, WSS, TURN creds + ICE,
entitlement-scoped features). DECISIONS NEEDED: TURN credential source (coturn static-secret REST?),
entitlement model. Tests.
- [ ] TDD: bootstrap response carries SIP identity + ICE/TURN + feature entitlements.

### G8: Webhook retry sweep (#8)
**Files:** confirm/implement a scheduled sweep (Celery beat or scheduler) that re-delivers failed
webhook rows, distinct from enqueue-time retry. Tests.
- [ ] TDD: a failed delivery row is picked up + retried by the sweep.

## Self-review
Covers the codeable gaps. Out of scope (need infra/carrier, tracked separately): media e2e proof,
queue caller<->agent media, PSTN/DID/trunking. #10 not reproducing.
