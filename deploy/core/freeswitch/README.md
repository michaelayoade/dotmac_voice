# FreeSWITCH/FusionPBX side of FS-in-path (DB-managed)

FusionPBX renders FreeSWITCH config via mod_xml_curl and **caches** it under
`/var/cache/fusionpbx/`. After any DB change, flush the relevant cache file and reload, e.g.:
- ACL: `rm /var/cache/fusionpbx/configuration.acl.conf && fs_cli -x reloadacl`
- Dialplan: `rm /var/cache/fusionpbx/dialplan.* && fs_cli -x reloadxml`

## Trust Kamailio (Task 1) — DONE
Kamailio (`10.10.10.1`) added to the **`providers`** access control (uuid
`aacb42b3-eb5f-4cb7-b6e8-03ea9306c05a`) as `allow 10.10.10.1/32`. The internal profile has
`apply-inbound-acl=providers` + `auth-calls=true`, so providers-matched IPs are trusted trunk
peers and bypass digest auth. Verified: `fs_cli -x 'acl 10.10.10.1 providers'` => true.

SQL (idempotent record; survives a future UI regen):
```sql
INSERT INTO v_access_control_nodes (access_control_node_uuid, access_control_uuid, node_type, node_cidr, node_description, insert_date)
VALUES (gen_random_uuid(), 'aacb42b3-eb5f-4cb7-b6e8-03ea9306c05a', 'allow', '10.10.10.1/32', 'Kamailio WebRTC edge (FS-in-path)', now());
```

## Gateway — NOT used
FS reaches Kamailio via a direct SIP URI bridge (`sofia/external/$dest@10.10.10.1:5060`); no
FusionPBX gateway object needed.

## Task 4 — local_extension bridge retargeted (DONE, in DB)
`local_extension` (global) dialplan detail (uuid parent `059f53f5-eb8b-40a1-9a0d-31d983c9ebd4`,
type=bridge, order=75) changed from `user/${destination_number}@${domain_name}` to
`sofia/external/${destination_number}@10.10.10.1:5060`. `continue_on_fail=true` + the following
`app.lua failure_handler` preserve the no-answer→voicemail fallback.
Revert: set it back to `user/${destination_number}@${domain_name}` + flush `dialplan.*` cache.

## OPEN BLOCKER (Task 5) — public vs domain context
With `ROUTE_VIA_FS` ON, Kamailio→FS works but FreeSWITCH routes the trusted-peer (providers ACL)
call into the **public context** (inbound-route/DID handler) → `[inbound routes] 404 ... 1002`,
NOT the `voicetest.dotmac` domain context where `local_extension` runs.
**Fix needed:** a public-context dialplan entry that, for source `10.10.10.1` + internal-extension
pattern, `transfer $1 XML <domain>` into the domain context (then local_extension + voicemail run).
Implement via FusionPBX DB (v_dialplans context='public' + details) or the web UI.

## State: ROLLED BACK to known-good (ROUTE_VIA_FS OFF), two-way audio verified. FS routes/ACL/bridge
retarget remain staged; flipping the flag is the only step that changes behavior.

## FS-in-path progress (2026-06-24) — ROUTING PROVEN, media-plane open

**Routing: WORKING end-to-end.** With ROUTE_VIA_FS on, a WS call now flows
WS-A -> Kamailio -> FreeSWITCH (FusionPBX dialplan executes) -> Kamailio -> WS-B and the
call CONNECTS. Path fixes that got us here:
- Public-context dialplan `kamailio-internal-to-domain` (order 50): bridges internal-extension
  calls from the trusted Kamailio peer directly to `sofia/external/${destination_number}@10.10.10.1:5060`
  with a voicemail fallback (bypasses the FusionPBX `user_exists`/domain-context machinery that
  404'd/480'd trunk-sourced calls). `continue_on_fail` + `answer` + `voicemail`.
- Kamailio FS-leg branch: `$rd = "voicetest.dotmac"` before `lookup()` — FS bridges to `@10.10.10.1`
  but users register `@voicetest.dotmac` (single-domain; multi-domain needs the domain in a header).

**rtpengine (EDGE) interfaces** (now): `pub/10.120.120.50!160.119.126.62` + `int/10.10.10.1`.
Backup: `/etc/rtpengine/rtpengine.conf.bak-fsinpath`. Direct WS<->WS path re-verified two-way
after this change (`direction=pub direction=pub`).

**OPEN — media plane.** With ROUTE_VIA_FS on the answered call connects but: callee webphone
reports "WebRTC Error" (the offer rtpengine builds toward WS-B is not valid WebRTC), and 0 RTP
packets flow on EITHER leg. Per-leg routes are staged (RTP_OFFER_TO_FS / REPLY_FROM_FS strip to
plain RTP toward FS via `direction=pub direction=int`; RTP_OFFER_TO_WS / REPLY_FROM_WS upgrade to
`UDP/TLS/RTP/SAVPF ICE=force DTLS=active` toward the WS client via `direction=int direction=pub`).
**Next:** capture rtpengine's actual offer/answer SDP for both B2BUA dialogs (two Call-IDs) and
debug the WebRTC offer toward WS-B + the 0-packet relay (systematic SDP-level, not flag guessing).

**State: ROLLED BACK to known-good (ROUTE_VIA_FS OFF); direct WS<->WS two-way audio verified.**
All FS-in-path routes are inert behind the flag.

## FS-in-path: TWO-WAY AUDIO THROUGH FREESWITCH ✅ (2026-06-24)

End-to-end WORKING: WS-A -> Kamailio -> FreeSWITCH (dialplan executes, bridges) -> Kamailio -> WS-B
with verified two-way RTP (harness: caller rx ~138 / callee rx ~66 pk). FS log confirms
`Channel sofia/external/1002@10.10.10.1:5060 has been answered, RINGING -> ACTIVE` (criterion 2).
Acceptance criteria 1,2,3 met; 6,7 (regression+rollback) verified (direct path 140/140 with flag off).

### The media-plane fixes (the hard part), all on EDGE Kamailio + rtpengine:
- **rtpengine interfaces** must be ONE semicolon-separated line (key-file parser keeps only the last
  `interface=` otherwise): `interface = pub/10.120.120.50!160.119.126.62;int/10.10.10.1`.
- **Per-leg transcoding** via direction-selected interfaces:
  - WS-caller<->FS (`RTP_OFFER_TO_FS`/`REPLY_FROM_FS`): `RTP/AVP ICE=remove direction=pub direction=int`
    (strip WebRTC to plain RTP toward FS) / answer back `UDP/TLS/RTP/SAVPF ICE=force DTLS=active`.
  - FS<->WS-callee (`RTP_OFFER_TO_WS`/`REPLY_FROM_WS`): `UDP/TLS/RTP/SAVPF ICE=force DTLS=active
    rtcp-mux-offer direction=int direction=pub` / answer back `RTP/AVP ICE=remove`.
- **`rtcp-mux-offer` was the final blocker:** FS's plain offer has no rtcp-mux, so the WebRTC offer
  rtpengine built toward the browser lacked `a=rtcp-mux`; browsers default to `rtcpMuxPolicy:require`
  and rejected it with `488 Not Acceptable Here` (surfaced as "WebRTC Error"). Forcing rtpengine to
  offer rtcp-mux fixed it.
- Use `rtpengine_manage` (not `rtpengine_offer/answer`) — note `$rb` shows the PRE-lump body, so log
  dumps look un-rewritten; the wire (tcpdump) shows the real rewritten SDP.

### State: flag OFF (safe). Flip `#!define ROUTE_VIA_FS` on for FS-in-path.
### REMAINING for full acceptance: criterion 5 (voicemail smoke test), criterion 4 (clean teardown),
### loop invariant (§11). Multi-domain: replace the single-domain `$rd` hardcode with a header.

## Voicemail smoke test (criterion 5) — WORKING (2026-06-24)
Call WS-A -> 1002 (unregistered) -> FS bridge fails -> continue_on_fail -> answer + voicemail.
Verified via harness (scratchpad/voicetest/harness-vm.js):
- Greeting plays TO WS-A: rx 816 pk; FS log: vm-person -> spell "1002" -> vm-not_available ->
  vm-record_message (full prompt sequence over the WS-A rtpengine leg).
- WS-A audio recorded: tx 611 pk; stored `WAVE PCM 16-bit mono 16kHz, 3.92s` valid file at
  /var/lib/freeswitch/storage/voicemail/default/voicetest.dotmac/1002/msg_*.wav
- `mod_voicemail.c: Deliver VM to 1002@voicetest.dotmac` + `Update MWI: Messages Waiting yes,
  Voice Message 1/0` (delivered + MWI set).

Prereqs done: created v_voicemails box for 1002; **mod_voicemail was NOT loaded** (missing from
v_modules AND modules.conf.xml) — added `<load module="mod_voicemail"/>` after mod_sofia in
/etc/freeswitch/autoload_configs/modules.conf.xml (backup .bak-voice) so it persists.

### Teardown (criterion 4): clean BYE works, abrupt WS-close leaks
- Clean hangup (btnHangup -> BYE): tears down cleanly (voicemail call left NO stray channel;
  rtpengine sessions = 0).
- Abrupt WS close (browser/tab close, no BYE): the two-way harness closes the browser without a
  BYE, leaving 1001<->1002 FS legs in CS_EXCHANGE_MEDIA until FS RTP-timeout reaps them.
  **FOLLOW-UP:** Kamailio `event_route[websocket:closed]` should locate + BYE the affected dialogs
  (or rely on shorter FS RTP/session timeout). Cleared the strays with `hupall`.

### FusionPBX portal voicemail row — FOLLOW-UP
mod_voicemail delivers + sets MWI, but no `v_voicemail_messages` row is inserted (FusionPBX's
voicemail.conf.xml `api-on-*` Lua hook / DB integration not wired on this source build), so the
self-care portal won't list the message yet. The recording itself is stored + playable.

## ACCEPTANCE: 1✅ 2✅ 3✅ 5✅(core; portal-row follow-up) 6✅ 7✅ | 4 = clean-BYE✅, abrupt-close follow-up
## State: ROUTE_VIA_FS OFF (safe/known-good). FS-in-path proven (two-way audio + voicemail), one flag-flip away.

## Follow-ups addressed (2026-06-24, part 2)

**(a) Abrupt-WS-disconnect teardown — FIXED.** Profile `rtp-timeout-sec` wasn't reaching the
generated sofia config, so instead set it per-call as a channel variable in the public dialplan:
`<action application="export" data="rtp_timeout_sec=30"/>` (+ rtp_hold_timeout_sec=1800). `export`
propagates it to the bridged b-leg, so BOTH FS legs reap ~30s after media stops. Verified: abrupt
browser close (no BYE) -> channels 2 -> 0 by t+40s. (Clean BYE still tears down instantly.)

**(c) Multi-domain registrar lookup — FIXED (mechanism).** FS bridge stamps the original domain:
`bridge {sip_h_X-Voice-Domain=${sip_req_host}}sofia/external/${destination_number}@10.10.10.1:5060`.
Kamailio FS-leg: `if (is_present_hf("X-Voice-Domain")) $rd = $hdr(X-Voice-Domain); else $rd =
"voicetest.dotmac";`. Removes the single-domain hardcode (fallback retained). Answered call verified
two-way via this path. (Full multi-domain needs a 2nd test domain to exercise.)

**(b) FusionPBX portal voicemail row — STILL DEFERRED.** Tried swapping the native `voicemail` app
for `lua app.lua voicemail` to get the `v_voicemail_messages` insert, but the FusionPBX voicemail Lua
needs the full call context FusionPBX's own failure_handler sets (it played no greeting + no row as a
drop-in). Reverted to the native `voicemail` app (works: greeting + record + playable wav + MWI).
Portal listing row remains a proper FusionPBX-voicemail-integration task (wire the failure_handler /
api-on hook), not a one-line change.

### Gotchas learned
- FusionPBX dialplan `${...}` vars get MANGLED through ssh->psql->heredoc quoting; load XML verbatim
  via `pg_read_file('/tmp/dp.xml')` instead. Repo copy: `deploy/core/freeswitch/kamailio-internal-to-domain.xml`.
- `${network_addr}`/`${sip_network_ip}` are EMPTY at dialplan parse-time (only set at action-execute);
  a `sofia profile rescan` can wedge parse-time vars — a full `systemctl restart freeswitch` restored
  `${network_addr}` matching. (Discriminator stays `${network_addr}`; providers ACL is the hard IP gate.)

## (b) FusionPBX portal voicemail row — RESOLVED (2026-06-24, part 3)
The portal listing row (`v_voicemail_messages`) now inserts. Two fixes:
1. **Leave-message via the FusionPBX voicemail Lua** (replicating the canonical `send_to_voicemail`
   dialplan), NOT the native `voicemail` app. The leave action is `voicemail_action=save` (an UNSET
   action is NOT "leave" — that was the earlier failure). Public dialplan now does:
   `answer -> sleep 1000 -> set voicemail_action=save -> set voicemail_id=${destination_number} ->
   set voicemail_profile=default -> set send_to_voicemail=true -> set domain_name=${sip_req_host} ->
   lua app.lua voicemail`.
2. **Fixed the broken storage path:** `v_default_settings` `switch/voicemail/dir` was `/voicemail`
   (the Lua appends `/default/<domain>` -> `Error Opening File`, size 0, no row). Set it to the real
   path `/var/lib/freeswitch/storage/voicemail`. **This DB fix is install-specific (apply on prod).**

Verified: greeting plays to WS-A, WS-A recorded, file stored at the correct path
(`.../storage/voicemail/default/voicetest.dotmac/1002/msg_*.wav`, valid WAVE PCM 16k), AND a
`v_voicemail_messages` row is created (portal will list it) + MWI set. (Test recording is ~1-2s
because the synthetic fake-mic tone trips silence detection; a real caller records to hangup.)

## ALL THREE FOLLOW-UPS DONE: (a) teardown ✅  (b) portal voicemail row ✅  (c) multi-domain ✅
## DEPLOYED 2026-06-24: ROUTE_VIA_FS ON (LIVE). Verified: answered call two-way (133/66) + voicemail w/ portal row. Rollback = comment the define + restart kamailio (direct path known-good).

## THE user-bridge unlock + ring groups (2026-06-24)
**Unlock (linchpin for all bridge-to-extension features):** WS extensions register on Kamailio, not
FS, so FusionPBX's `user/<ext>` bridges resolve to an empty FS contact. Fix: set each extension's
`dial_string` to route to Kamailio:
`{sip_invite_domain=${domain_name},sip_h_X-Voice-Domain=${domain_name}}sofia/external/${dialed_user}@10.10.10.1:5060`
(see `dialstring-unlock-and-1003.sql`). Safe: the direct 1xxx path bridges sofia/external directly
(doesn't use user/). This unlocks ring groups, queues, IVR-to-ext, transfers, local_extension.
Created extension 1003 (3rd member) for multi-party tests.

**Ring group (2000):** public dialplan bridges `user/1002,user/1003` (simultaneous). Verified:
1001->2000 rings both, 1003 answered, caller two-way (rx883/tx634), 1003 rx492/tx888.

## Tested-working features (FS-in-path): registration, internal call (direct+through-FS), TURN,
## voicemail+portal row, teardown, multi-domain, CDR, **call recording**, **conference**, **ring group**.

## Call-center queue (5000) — distribution WORKS, media bridge WIP (2026-06-24)
mod_callcenter loaded. Queue 5000 + agents 1002/1003 (callback, dial via user/<ext> -> Kamailio).
Setup gotchas: (1) queue name in callcenter.conf = `queue_name@domain` (set queue_name='5000', NOT
queue_extension); (2) config cache key uses a COLON `configuration:callcenter.conf` (flush with
`find /var/cache/fusionpbx -iname '*callcenter*' -delete`); (3) agents/tiers are loaded via runtime
`callcenter_config agent add <uuid> / tier add <ext>@<domain> <uuid>` (NOT persisted across FS
restart — FusionPBX re-applies via a job; raw setup needs re-running queue load + agent/tier adds).
VERIFIED: 1001->5000 queued, agent 1003 rung via the unlock + answered. OPEN: caller<->agent media
is one-way (both WS legs send to FS, neither receives back) — needs per-leg rtpengine debugging in
the callcenter bridge (same class as the original FS-in-path media work; also gates hold/transfer
in-dialog re-INVITE handling). Files: queue-setup.sql, kamailio-queue.xml.

## Queue media debug (2026-06-24, pass a)
- FIXED a real bug: REPLY_FROM_WS once-guard relayed RAW WebRTC SDP on retransmitted 200 OKs from
  FS-originated legs (queue/ring-group agents) -> FS latched the unreachable browser address. Removed
  the guard on the FS-facing answer (plain RTP, no DTLS to reset). Live path re-verified two-way.
- RULED OUT transcode (forcing PCMU on both legs didn't fix it).
- ROOT CAUSE (open): mod_callcenter bridges the pre-answered, queued caller to the callback agent and
  RE-ANCHORS media (`switch_ivr_bridge CS_CONSUME_MEDIA->HIBERNATE->CS_RESET`); with both legs
  rtpengine-anchored WebRTC, the FS->WS direction breaks on both legs (one-way). Ring group works
  because it's a DIRECT bridge (no pre-answer-into-queue, no callback re-anchor). Needs deeper
  mod_callcenter media handling (bypass/proxy flags or a non-pre-answer queue pattern) OR validation
  with a real softphone (may be specific to the synthetic webphone's re-negotiation handling).
