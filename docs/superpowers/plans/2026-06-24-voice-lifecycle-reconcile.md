# Voice Lifecycle + Feature Reconcile — Implementation Plan

> Implement task-by-task with TDD. Builds on the control-plane feature extension (T1–T10).

**Goal:** Make every PBX feature first-class desired-state — drift-reconciled, updatable, deletable —
plus customer-level **suspend/resume** and **deprovision**. Closes the lifecycle + feature-reconcile
gaps so the control plane can bill, suspend, and offboard, not just create.

**Architecture:** Add desired-state models (RingGroup/IvrMenu/ConferenceRoom/Queue + children) keyed by
`voice_domain_id`. `FusionpbxClient` gains delete primitives + domain-tagged listing for drift.
`reconcile_voice` becomes the single apply: present models → `ensure_*` (idempotent create/update);
absent → delete. Suspend = `VoiceDomain.is_active=False` → reconcile treats the customer's desired
extensions/features as empty (removes them from FusionPBX so they can't register/route) while keeping
the dotmac_voice models; resume re-applies. Update is free (primitives upsert).

**Tech:** same as before — SQLAlchemy Core client (SQLite-injectable tests), alembic, pytest, require_ingress.

## Global Constraints
- Idempotent everywhere; reload only on change. Delete primitives reload only if a row was removed.
- Managed dialplans tagged `dialplan_description = "dotmac-voice:managed:<domain>"` so a domain's
  features are listable (queues use their real `domain_uuid`). Drift = listed-but-not-desired → delete.
- Suspend must work with the current auth path: removing a customer's FusionPBX extensions makes
  `v_kam_subscriber` return nothing → Kamailio registration fails. Models are preserved for resume.
- Migrations via `make migrate-new`; new model fields/tables get a migration.

---

### Task L1: Delete primitives
**Files:** `app/services/fusionpbx/client.py` (+ `delete_dialplan(name) -> bool`, `delete_queue(domain,
number) -> bool` removing v_call_center_* rows + dialplan + best-effort `callcenter_config queue unload`,
`delete_voicemail(domain, number) -> bool`); `tests/test_fusionpbx_client.py`.
**Interfaces:** each returns True if it removed something (reload/commander only then).
- [ ] TDD: test delete_dialplan removes a managed row + is a no-op when absent; same for delete_queue
  (rows+dialplan+unload command issued) and delete_voicemail. Tag dialplans on create with the domain.

### Task L2: Suspend / resume
**Files:** `app/models/voice.py` (`VoiceDomain.is_active: bool = True`); migration;
`app/services/reconcile/voice.py` (when `not is_active`: desired extensions = ∅ → existing delete loop
removes them, skip voicemail/routing/feature apply); `app/api/provisioning.py` or a small endpoint
(`POST /provisioning/domains/{cid}/suspend` + `/resume` toggling `is_active` + reconcile);
`tests/test_reconcile_voice.py`, provisioning test.
- [ ] TDD: suspended domain reconcile deletes the customer's FusionPBX extensions (fake records),
  models preserved; resume recreates. Endpoint toggles + reconciles.

### Task L3: Feature desired-state models
**Files:** `app/models/voice.py` (`ConferenceRoom`, `RingGroup` + `RingGroupMember`, `IvrMenu` +
`IvrOption`, `Queue` + `QueueAgent` — all `voice_domain_id` FK + `number` + params); migration;
`app/schemas/voice.py` (intents). Tests: model creation.
- [ ] TDD: models persist desired feature state; `DomainIntent`-style nested schemas validate.

### Task L4: Reconcile features (apply + drift-delete)
**Files:** `client.py` (`list_managed_dialplans(domain) -> set[str]` by the description tag;
`list_queues(domain) -> set[str]` by domain_uuid); `reconcile/voice.py` (after extensions: for each
feature type, ensure present-model features, delete listed-but-undesired); `tests/test_reconcile_voice.py`.
**Interfaces:** reconcile applies + removes features to match the models exactly.
- [ ] TDD: domain with a ring group + IVR model → reconcile creates them; remove the IVR model →
  reconcile deletes the IVR dialplan; orphan dialplan present → reconcile deletes it.

### Task L5: Deprovision + feature desired-state API
**Files:** `app/api/voice_features.py` (PUT-replace desired set per feature type, or DELETE one;
`DELETE /provisioning/domains/{cid}` → mark inactive/empty → reconcile removes all → delete domain
models); `client.py` (`delete_domain` optional); tests.
- [ ] TDD: PUT features replaces the set (add/remove); DELETE customer reconciles to empty (all
  features + extensions removed from FusionPBX) then drops the dotmac_voice domain row.

## Self-review
Covers: delete (L1), suspend/resume (L2), feature desired-state (L3), drift reconcile incl. delete (L4),
deprovision + declarative feature API (L5). Update is implicit (primitives upsert). Out of scope (later):
DID/trunking, multi-tenant per-domain number isolation, runtime FS-restart resync.
