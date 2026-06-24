# FreeSWITCH-in-Path Routing Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route all WebRTC calls through FreeSWITCH's dialplan (`WS → Kamailio → FS → Kamailio → WS`) so PBX features work, without regressing the validated two-way audio, with a one-flag rollback.

**Architecture:** Kamailio stays the WebRTC registrar/edge; FreeSWITCH becomes the PBX/media core; they federate over trusted internal SIP/RTP (`10.10.10.1` ↔ `10.10.10.10`). Every change is **additive and behind `#!define ROUTE_VIA_FS`** — the current direct WS↔WS path remains until the flag is flipped, and flipping it back is instant.

**Tech Stack:** Kamailio 5.6 (`kamailio.cfg` on EDGE), FreeSWITCH 1.10.12 + FusionPBX (CORE), rtpengine, coturn, Puppeteer harness (`scratchpad/voicetest/harness.js`).

## Global Constraints (verbatim from the spec)

- FreeSWITCH must **never** be asked to bridge to `transport=ws` users directly; user legs route to `sofia/gateway/kamailio/$dest`, never `user/…`.
- Kamailio and FreeSWITCH trust each other **by IP/ACL** (`10.10.10.1` ↔ `10.10.10.10`), not by registering WS users on FS. WS users keep authenticating to Kamailio.
- **An FS-originated b-leg is never reclassified as a new WS/user call and is never routed back to FS** (source-IP is the hard discriminator).
- The FusionPBX gateway-rewrite applies **only** to the internal registered-extension pattern, ordered **after** feature-code/voicemail/IVR/conference/emergency/trunk dialplan entries, and **logs** when it fires.
- **No media bypass** in two places: FS profile/bridge (`bypass_media` off) AND rtpengine anchors both WebRTC-facing legs.
- rtpengine per leg: WS-caller `SRTP/ICE/DTLS↔RTP`; FS side plain RTP; WS-callee `RTP↔SRTP/ICE/DTLS`.
- Rollback flag must be operationally simple: edit `#!define ROUTE_VIA_FS` + `systemctl restart kamailio` (documented). Restart acceptable for v1.

**Access (every task uses these):**
- EDGE (Kamailio/rtpengine/coturn): `ssh -F ~/dotmac-network/ssh-keys/config -o ProxyJump=proxmox -i ~/dotmac-network/ssh-keys/proxmox-server root@10.120.120.50`
- CORE (FreeSWITCH/FusionPBX/app): `…root@10.10.10.10`
- `fs_cli` password: `0c749d18717b1651376802369e65d0a8ba5b0902` (use `fs_cli -p <pw> -x '<cmd>'`)
- Working Kamailio config source of truth: `scratchpad/voicetest/../kamailio.cfg` (deploy via `cat > /etc/kamailio/kamailio.cfg.dmnew`, `kamailio -c -f`, then `mv` + restart). Harness: `scratchpad/voicetest/harness.js` (`RELAY=0|1`, `PASS=`, real ext passwords 1001=`Hxuuo9MOPBXuxQUOn85ebg`, 1002=`yCqjcTQ0VoG1P8670zPXvQ`).
- Repo target for committed infra: create `deploy/edge/kamailio.cfg`, `deploy/core/freeswitch/`, `deploy/harness/` and commit the real configs (closes the "configs only on servers" gap).

---

### Task 0: Baseline snapshot + known-good proof

**Files:** Create `deploy/edge/kamailio.cfg` (copy of current working config), `deploy/README.md`.

- [ ] **Step 1: Back up live configs**
  EDGE: `cp /etc/kamailio/kamailio.cfg /etc/kamailio/kamailio.cfg.pre-fsinpath`.
  CORE: `cp -a /etc/freeswitch/autoload_configs /root/fs-config-backup-$(date +%s)` and `cp -a /etc/freeswitch/dialplan /root/fs-dialplan-backup-$(date +%s)`.

- [ ] **Step 2: Capture current working Kamailio config into the repo**
  `scp` the live `/etc/kamailio/kamailio.cfg` to `deploy/edge/kamailio.cfg` in the repo.

- [ ] **Step 3: Prove the known-good baseline**
  Run: `cd scratchpad/voicetest && RELAY=0 node harness.js`
  Expected: `RESULT: ✅ TWO-WAY AUDIO`. This is the regression/rollback target.

- [ ] **Step 4: Commit**
  `git add deploy/ && git commit -m "chore(voice): snapshot working Kamailio config + deploy dir before FS-in-path"`

---

### Task 1: FreeSWITCH trusts Kamailio (ACL) + Kamailio gateway

**Files:** Modify (CORE) the internal sofia profile (`/etc/freeswitch/sip_profiles/internal.xml` or FusionPBX-managed) + add gateway `/etc/freeswitch/sip_profiles/external/kamailio.xml`. Mirror into repo `deploy/core/freeswitch/`.

**Interfaces:**
- Produces: a `kamailio` sofia gateway (`sofia/gateway/kamailio/<dest>` dialable) pointing at `10.10.10.1:5060`, `register=false`; the internal profile accepts SIP from `10.10.10.1` without auth.

- [ ] **Step 1: Add the Kamailio gateway**
  Create `/etc/freeswitch/sip_profiles/external/kamailio.xml`:
```xml
<include>
  <gateway name="kamailio">
    <param name="proxy" value="10.10.10.1:5060"/>
    <param name="register" value="false"/>
    <param name="caller-id-in-from" value="true"/>
  </gateway>
</include>
```

- [ ] **Step 2: Trust Kamailio's IP on the internal profile**
  In FusionPBX: Advanced → Access Controls → add/confirm a list (e.g. `lan`/a `voice-edge` list) with a node `allow 10.10.10.1/32`; set the **internal** profile `apply-inbound-acl` to that list. (Or edit the internal profile XML `<param name="apply-inbound-acl" value="voice-edge"/>` + define the ACL in `acl.conf.xml`.)

- [ ] **Step 3: Reload + verify gateway up**
  Run: `fs_cli -p <pw> -x 'sofia profile external rescan reloadxml'` then `fs_cli -p <pw> -x 'reloadacl'` then `fs_cli -p <pw> -x 'sofia status gateway kamailio'`
  Expected: gateway state `NOREG`/up, proxy `10.10.10.1`.

- [ ] **Step 4: Verify FS accepts SIP from Kamailio**
  From EDGE: `sipsak -s sip:test@10.10.10.10:5060 -v` (or originate an OPTIONS). Expected: a SIP response (not silently dropped) — confirms the ACL trusts `10.10.10.1`.

- [ ] **Step 5: Confirm no impact on the live path**
  Run: `cd scratchpad/voicetest && RELAY=0 node harness.js` → still `✅ TWO-WAY AUDIO` (Kamailio still routes direct; FS changes are inert).

- [ ] **Step 6: Commit**
  Mirror the gateway XML + ACL note into `deploy/core/freeswitch/`. `git add deploy/ && git commit -m "feat(voice): FS kamailio gateway + trust Kamailio IP (ACL)"`

---

### Task 2: Kamailio handles FS-originated user legs (FS → WS delivery)

**Files:** Modify `deploy/edge/kamailio.cfg` (the working config). This route is **additive** — it only fires for SIP sourced from FS (`10.10.10.10`), which does not happen until Task 4's flag is on, so the live path is unaffected.

**Interfaces:**
- Consumes: existing `lookup("location")`, `handle_ruri_alias()`, `route(RTPMANAGE)` (rtpengine WebRTC flags), `route(RELAY)`.
- Produces: a top-of-`request_route` branch that, for initial requests from `10.10.10.10`, delivers to the WS user.

- [ ] **Step 1: Add FS source trust + FS-leg routing**
  In `request_route`, before the existing REGISTER/INVITE handling, add (after `t_check_trans()`):
```
    # FS-originated leg (trusted peer): deliver to the WS user, never back to FS
    if ($si == "10.10.10.10") {
        if (is_method("INVITE")) {
            xlog("L_INFO", "[VOICE] FS-leg INVITE -> WS user $rU\n");
            if (!lookup("location")) { sl_send_reply("404", "User Offline"); exit; }
            handle_ruri_alias();
            record_route();
            route(RTPMANAGE);              # rtpengine: plain RTP (FS) -> WebRTC (WS-B)
            t_on_reply("MANAGE_REPLY");
            t_on_failure("MANAGE_FAILURE");
            route(RELAY);
            exit;
        }
    }
```
  (No digest auth for `$si==10.10.10.10` — IP-trusted peer. The discriminator is source IP; this branch never routes to FS, satisfying the loop invariant.)

- [ ] **Step 2: Config-check + deploy**
  Run the deploy pattern: `kamailio -c -f .../kamailio.cfg.dmnew` (Expected: no errors) → `mv` → `systemctl restart kamailio`.

- [ ] **Step 3: Verify FS→WS delivery in isolation**
  Register 1002 (`cd scratchpad/voicetest && node -e` register helper, or the harness register step). From CORE: `fs_cli -p <pw> -x 'originate sofia/gateway/kamailio/1002 &echo'`.
  Expected: 1002's webphone receives an INVITE (Kamailio delivered the FS-originated leg over WS); `kamcmd` shows an rtpengine session for the WS-B leg. (Audio is echo from FS.)

- [ ] **Step 4: Regression**
  `RELAY=0 node harness.js` → still `✅ TWO-WAY AUDIO`.

- [ ] **Step 5: Commit**
  `git add deploy/edge/kamailio.cfg && git commit -m "feat(voice): Kamailio delivers FS-originated legs to WS users (IP-trusted)"`

---

### Task 3: Kamailio routes WS-originated calls to FS, behind `ROUTE_VIA_FS` (flag OFF)

**Files:** Modify `deploy/edge/kamailio.cfg`.

**Interfaces:**
- Consumes: existing WS auth (`route(AUTH_INVITE)`), `route(RTPMANAGE)`, `route(RELAY)`.
- Produces: when `ROUTE_VIA_FS` is defined, WS-originated INVITEs go to `sip:$rU@10.10.10.10:5060`; when not, the current direct `lookup` path runs unchanged.

- [ ] **Step 1: Add the define (OFF initially) + the FS route in the INVITE branch**
  Near the top: `#!define ROUTE_VIA_FS` **commented out** (`# #!define ROUTE_VIA_FS`).
  In the initial-INVITE handling (the existing `if (is_method("INVITE"))` for WS, after `route(AUTH_INVITE)`), wrap the destination selection:
```
#!ifdef ROUTE_VIA_FS
        xlog("L_INFO", "[VOICE] WS INVITE $fU -> FS (route-via-fs)\n");
        record_route();
        route(RTPMANAGE);                 # rtpengine: WebRTC (WS-A) -> plain RTP (FS)
        t_on_reply("MANAGE_REPLY");
        t_on_failure("MANAGE_FAILURE");
        $du = "sip:10.10.10.10:5060";
        append_hf("X-Voice-Edge: kamailio\r\n");
        route(RELAY);
        exit;
#!endif
        # (fall through to the existing direct lookup() path when flag is OFF)
```

- [ ] **Step 2: Config-check + deploy (flag still OFF)**
  Deploy. With `ROUTE_VIA_FS` commented, behavior is identical to today.

- [ ] **Step 3: Prove no behavior change (flag OFF)**
  `RELAY=0 node harness.js` → `✅ TWO-WAY AUDIO` (still the direct path).

- [ ] **Step 4: Commit**
  `git add deploy/edge/kamailio.cfg && git commit -m "feat(voice): WS->FS route behind ROUTE_VIA_FS flag (off by default)"`

---

### Task 4: FusionPBX dialplan — narrow internal-extension → Kamailio gateway

**Files:** Add a FusionPBX dialplan entry (default context) via the UI or `/etc/freeswitch/dialplan/default/`. Mirror into `deploy/core/freeswitch/dialplan/`.

**Interfaces:**
- Consumes: the `kamailio` gateway (Task 1).
- Produces: internal-extension-pattern destinations bridge to `sofia/gateway/kamailio/$dest`, ordered after features/voicemail/IVR/conference/emergency/trunk.

- [ ] **Step 1: Determine the exact internal extension pattern**
  Run: `sudo -u postgres psql -d fusionpbx -tAc "SELECT DISTINCT extension FROM v_extensions ORDER BY 1"`.
  Build a tight regex for the real plan (test extensions are 1001/1002 → start with `^(1[0-9]{3})$`; widen to the actual plan). Record the chosen regex.

- [ ] **Step 2: Add the dialplan entry (ordered + logged)**
  In FusionPBX (Dialplan → Dialplan Manager, context `default`, high `order` number so it runs AFTER feature/voicemail/IVR/conference/emergency/outbound entries), create a dialplan with condition `destination_number` `^(1[0-9]{3})$` and actions:
```xml
<action application="log" data="INFO voice-edge: routing ${destination_number} to kamailio gateway"/>
<action application="bridge" data="sofia/gateway/kamailio/${destination_number}"/>
```
  Confirm it is BELOW the existing voicemail/feature/IVR entries in the XML order (`fs_cli -p <pw> -x 'xml_locate dialplan'` or inspect the generated `/etc/freeswitch/dialplan/default.xml`).

- [ ] **Step 3: Reload + verify ordering**
  `fs_cli -p <pw> -x 'reloadxml'`. Inspect generated dialplan: the gateway rule appears after voicemail/feature entries.

- [ ] **Step 4: Verify a feature code is NOT rewritten**
  `fs_cli -p <pw> -x 'originate loopback/*97 &park'` (or the configured voicemail-access code) → FS handles it locally (not bridged to the gateway). Expected: no "voice-edge: routing" log for `*97`.

- [ ] **Step 5: Commit**
  Mirror the dialplan XML into `deploy/core/freeswitch/dialplan/` and `git commit -m "feat(voice): FusionPBX dialplan routes internal extensions to Kamailio gateway (narrow + logged)"`

---

### Task 5: Flip `ROUTE_VIA_FS` ON — full path + answered-call acceptance (crit 1–3)

**Files:** Modify `deploy/edge/kamailio.cfg` (uncomment the define).

- [ ] **Step 1: Enable the flag**
  Uncomment `#!define ROUTE_VIA_FS`. Deploy (config-check + restart).

- [ ] **Step 2: Answered call through FS**
  `cd scratchpad/voicetest && RELAY=0 node harness.js` (1001 calls 1002, 1002 auto-answers).
  Expected: `RESULT: ✅ TWO-WAY AUDIO`. (crit 1, 3)

- [ ] **Step 3: Prove FS executed (crit 2)**
  During the call: `fs_cli -p <pw> -x 'show channels'` shows the two legs on FS; FS log shows the `voice-edge: routing 1002 to kamailio gateway` line and dialplan execution.
  Expected: FS visibly in the path (not a Kamailio-direct bridge).

- [ ] **Step 4: Verify two rtpengine sessions + clean media**
  On EDGE during the call: `rtpengine-ctl list numsessions` → 2 (one per WS leg). Confirm both WS legs show packets both directions.

- [ ] **Step 5: Commit**
  `git add deploy/edge/kamailio.cfg && git commit -m "feat(voice): enable ROUTE_VIA_FS — all calls flow through FreeSWITCH"`

---

### Task 6: Clean teardown — hangup / busy / failed destination (crit 4) + loop invariant (§11)

**Files:** Extend `scratchpad/voicetest/harness.js` (mirror to `deploy/harness/`).

- [ ] **Step 1: Add teardown assertions to the harness**
  After each call: assert `rtpengine-ctl list numsessions` returns to 0 and `fs_cli show channels` is empty within ~3s of hangup. Add a `callNoAnswer()` and `callBadDest()` (dial an unregistered/invalid ext) path.

- [ ] **Step 2: Run hangup/busy/failed cases**
  Run the harness teardown suite.
  Expected: normal hangup → both legs + both rtpengine sessions gone; failed destination (e.g. dial `1999` unregistered) → caller gets a clean failure (FS returns 404/480 via the gateway), no stuck channels/sessions.

- [ ] **Step 3: Assert the loop invariant**
  Parse FS + Kamailio logs for the test: an FS→Kamailio INVITE for an extension is delivered to the WS user or fails cleanly, and there is **no** second INVITE from Kamailio back to FS for the same leg.
  Expected: zero FS→Kamailio→FS loops.

- [ ] **Step 4: Commit**
  `git add deploy/harness/ scratchpad/voicetest/harness.js && git commit -m "test(voice): teardown + loop-invariant assertions for FS-in-path"`

---

### Task 7: Voicemail smoke test (crit 5)

**Files:** FusionPBX voicemail config (enable on the test extension); extend the harness.

- [ ] **Step 1: Enable voicemail on the test extension**
  In FusionPBX, ensure extension 1002 has voicemail enabled with a greeting (record one, or accept the default), and the no-answer timeout routes to voicemail.

- [ ] **Step 2: Place the no-answer call via the harness**
  WS-A (1001) calls 1002; 1002 does NOT register/answer. FS no-answer → voicemail.
  Expected (harness): WS-A's `getStats` shows inbound RTP (the greeting) from FS; the call records and ends.

- [ ] **Step 3: Verify the recording was stored**
  Run: `find /var/lib/freeswitch/storage/voicemail -name '*.wav' -newermt '-2 min'` (or the FusionPBX voicemail dir / `v_voicemail_messages` table).
  Expected: a new message file with duration > 0.

- [ ] **Step 4: Confirm intelligibility (human)**
  Play back the stored message (FusionPBX UI → Voicemail, or `fs_cli` playback) and confirm intelligible audio.
  Expected: WS-A's recorded speech is clear. **This is the end-to-end proof: signaling + FS media + dialplan + recording.**

- [ ] **Step 5: Commit**
  `git add deploy/ scratchpad/voicetest/harness.js && git commit -m "test(voice): voicemail smoke test proves FS owns media + recording"`

---

### Task 8: Rollback verification (crit 6, 7) + runbook

**Files:** `deploy/README.md` (runbook), `deploy/edge/kamailio.cfg`.

- [ ] **Step 1: Disable the flag + restart**
  Comment `#!define ROUTE_VIA_FS`. Deploy (config-check + `systemctl restart kamailio`).

- [ ] **Step 2: Prove direct path restored**
  `RELAY=0 node harness.js` → `✅ TWO-WAY AUDIO` via the direct Kamailio path (FS no longer in the path; `fs_cli show channels` empty during the call).
  Expected: known-good audio with zero FS involvement. (crit 6, 7)

- [ ] **Step 3: Re-enable for production**
  Uncomment the flag, deploy, re-confirm the answered call works through FS.

- [ ] **Step 4: Write the runbook**
  In `deploy/README.md`: the rollback procedure (edit define → `kamailio -c` → restart → harness check), the FS gateway/dialplan/ACL summary, and a note that runtime no-restart toggle via `kamcmd` is a future optimization.

- [ ] **Step 5: Commit**
  `git add deploy/ && git commit -m "docs(voice): FS-in-path rollback runbook + verification"`

---

## Self-Review

**Spec coverage:** §4 trust → Task 1; §5 directionality → Tasks 2,3; §6 flows → Tasks 2–5,7; §7 FS/dialplan → Tasks 1,4; §8 bypass → Tasks 1,5; §9 rtpengine ownership → Tasks 2,3,5 (RTPMANAGE per leg); §10 rollback → Tasks 3,8; §11 loop guard → Task 6; §12 testing → Tasks 5–8; acceptance crit 1–7 all mapped. No gaps.

**Placeholders:** The internal-extension regex is determined by live inspection in Task 4 Step 1 (with a concrete starting value `^(1[0-9]{3})$` matching the real test extensions) — a real determined-at-implementation parameter, not a vague placeholder. FusionPBX ACL/voicemail UI steps reference the actual menus. No TBDs.

**Type/name consistency:** `ROUTE_VIA_FS`, `sofia/gateway/kamailio/$dest`, `$si=="10.10.10.10"`, `route(RTPMANAGE)`/`MANAGE_REPLY`/`MANAGE_FAILURE`, `X-Voice-Edge` header — used consistently across tasks and match the live config's existing route names.
