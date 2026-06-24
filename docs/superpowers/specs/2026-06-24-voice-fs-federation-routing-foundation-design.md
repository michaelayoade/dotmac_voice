# DotMac Voice — FreeSWITCH-in-Path Routing Foundation (Kamailio↔FS Federation)

**Date:** 2026-06-24
**Status:** Approved design — ready for implementation plan
**Scope:** Routing foundation only (make all calls flow through FreeSWITCH's dialplan without
regressing the validated WebRTC two-way audio). Full PBX feature configuration is explicitly a
later phase; this design proves the path with **one** representative feature (voicemail).

## 1. Context

Today the EDGE Kamailio is the WebRTC registrar and routes WS↔WS calls **directly** (Kamailio
usrloc lookup + rtpengine), bypassing FreeSWITCH. That delivers working two-way audio (validated
headlessly, normal + symmetric-NAT/TURN) but means **no PBX features** (voicemail, IVR, queues,
recording, transfers) are in the call path.

An earlier attempt to make FreeSWITCH the registrar (FS-in-path) failed: FS stored the WebRTC
contact with `transport=ws`, then tried to open a WebSocket to Kamailio's plain-SIP Path address on
the callee leg → instant `503`. That failure mode is the primary thing this design must avoid.

## 2. Goals / Non-goals

**Goals**
- All calls flow `WS client → Kamailio → FreeSWITCH → Kamailio → WS client`, with FreeSWITCH owning
  the dialplan and media.
- Two-way audio preserved (no regression).
- Prove FreeSWITCH dialplan + media ownership end-to-end via **voicemail**.
- Instant, safe rollback to the current direct WS↔WS path.

**Non-goals (this phase)**
- Full feature configuration (IVR trees, **call queues**, recording policy, transfers/attended
  transfer, presence/BLF). Queues especially are deferred (agent state, ring strategies, timeouts,
  reporting).
- PSTN/DID trunking.
- Billing/rating integration changes.

## 3. Acceptance criteria

1. WS extension A can call WS extension B **through Kamailio + FreeSWITCH**.
2. FreeSWITCH dialplan is **visibly executing** for the call (FS channel/dialplan logs show it).
3. Two-way audio works (RTP both directions, confirmed via getStats on each WS leg).
4. Hangup, busy/no-answer, and failed-destination behavior are clean (no stuck channels/sessions).
5. **Voicemail smoke test (concrete):** Call WS-A → WS-B through FS. If WS-B does not answer,
   FreeSWITCH plays B's voicemail greeting **to WS-A**, records audio from WS-A, stores the message,
   and the recording is playable with intelligible audio (intelligibility confirmed by playback/listen).
6. **No regression** to the existing WebSocket audio path.
7. **Rollback verification:** with `ROUTE_VIA_FS` disabled and Kamailio restarted, direct WS-A → WS-B
   audio still works.

## 4. Architecture & trust (req 7)

- **Kamailio** (EDGE; internal `10.10.10.1`, WS from nginx on `127.0.0.1:8080`) — WebRTC
  registrar/auth/edge, routing brain, rtpengine control.
- **FreeSWITCH/FusionPBX** (CORE `10.10.10.10`) — dialplan / features / media core.
- They federate over the internal `10.10.10.0/24` link via **plain SIP/RTP**, trusting each other
  **by IP/ACL**:
  - Kamailio **skips digest auth** for SIP sourced from `10.10.10.10` (FS is a trusted peer).
  - FS internal sofia profile gets `apply-inbound-acl` trusting `10.10.10.1` (Kamailio).
  - **WS users keep authenticating to Kamailio** (REGISTER + initial-INVITE digest). FreeSWITCH
    does **not** hold WS registrations and is never treated as the registrar.

## 5. SIP directionality (req 1, addition 1) — the invariant

Because FreeSWITCH is a **B2BUA**, the WS-A↔FS dialog and the FS↔WS-B dialog are **separate dialogs
with different Call-IDs**. Kamailio handles each independently:

- **WS-originated initial INVITE** (`proto==WS`, not from FS) → goes **to FS**.
- **FS-originated user b-leg** (`src_ip == 10.10.10.10`) → goes to **Kamailio usrloc lookup → WS
  user**.
- **An FS-originated b-leg is NEVER reclassified as a new WS/user-originated call** and is **never
  routed back to FS**. Source IP is the hard discriminator; an `X-Voice-Loop`/`X-Voice-Edge` header
  is a secondary guard, and `max_forwards` is the backstop.

## 6. Call flows

### 6.1 WS-A → ext B (answered)
1. `WS-A →(WSS)→ nginx → Kamailio`. Authenticated initial INVITE, R-URI = B.
2. Kamailio (WS-inbound route): `rtpengine_offer` on the **WS-A leg** (WebRTC SRTP/ICE/DTLS → plain
   RTP toward FS); `INVITE sip:B@10.10.10.10:5060`, stamp `X-Voice-Edge: kamailio`.
3. FS (FusionPBX dialplan **executes**): for internal-extension B (see §7 matching rule), bridge to
   `sofia/gateway/kamailio/B` (plain RTP). This emits a **new** INVITE (new Call-ID) toward Kamailio.
4. Kamailio (FS-originated route, `src_ip==FS`): `lookup("location")` for B → `handle_ruri_alias()`
   → `rtpengine_offer` on the **WS-B leg** (plain RTP → WebRTC) → deliver over WS to WS-B.
5. WS-B 200 OK → Kamailio `rtpengine_answer` (WS-B leg) → FS → Kamailio `rtpengine_answer` (WS-A
   leg) → WS-A.
6. Media: `WS-A ↔ rtpengine ↔ FS ↔ rtpengine ↔ WS-B`. **FS anchors media** (no bypass, §8).

### 6.2 WS-A → ext B (no answer → voicemail)
Steps 1–3 as above. WS-B does not answer (timeout/no contact). FS dialplan falls through to
**FusionPBX voicemail** for B: answers the WS-A↔FS leg, plays B's greeting **to WS-A** (FS media via
the WS-A rtpengine leg), records WS-A's audio, stores the message. No b-leg to Kamailio is needed;
the existing WS-A↔FS leg carries the voicemail media.

## 7. FreeSWITCH / FusionPBX changes (req 1, 2, 6; addition 2)

- **Gateway `kamailio`** in the internal sofia profile: `register=false`, `proxy=10.10.10.1:5060`,
  IP-trusted peer (no registration, no auth).
- **`apply-inbound-acl`** on the internal profile trusting `10.10.10.1`.
- **Media bypass OFF** (`inbound-bypass-media=false`; bridges must not set `bypass_media=true`) so FS
  genuinely anchors media (req 6, first of two places — see §8).
- **Dialplan rewrite — narrowly scoped (addition 2):** a dialplan entry that bridges to
  `sofia/gateway/kamailio/${destination_number}` **only** when `destination_number` matches the
  **internal registered-extension pattern** (the actual extension digit-plan, e.g. `^(1[0-9]{3})$`
  for 1000–1999 — finalized to the real plan during implementation). It is ordered **after**
  FusionPBX's feature-code / voicemail-access / IVR / conference / emergency / outbound-trunk
  dialplan entries so those match first and **never** get rewritten to the gateway. The rule
  **logs** when it fires (observability — `log INFO voice-edge: routing <ext> to kamailio gateway`).
- Voicemail enabled on the test extension (FusionPBX default voicemail app).

## 8. Media bypass — confirmed in both places (addition 3)

1. **FreeSWITCH/FusionPBX:** media bypass disabled (profile + bridge), so FS stays in the media path.
2. **Kamailio/rtpengine:** both WebRTC-facing legs are anchored/transcoded by rtpengine (WS-A leg
   and WS-B leg). FS never receives DTLS-SRTP/ICE; it only ever sees plain RTP.

## 9. rtpengine per-leg + session ownership (req 5; addition 5)

Per-leg media (req 5):
- **WS-caller leg:** SRTP/ICE/DTLS ↔ plain RTP (existing WebRTC flags: `ICE=force`, DTLS, the
  TURN/coturn + garki-core hairpin SNAT).
- **FS side:** plain RTP (no WebRTC flags).
- **WS-callee leg:** plain RTP ↔ SRTP/ICE/DTLS.

Two rtpengine sessions per user↔user call (one per WS-facing dialog). Ownership:

| Dialog | rtpengine session | Created by | Finalized by | Torn down by |
|---|---|---|---|---|
| WS-A ↔ FS | WS-A WebRTC↔RTP | WS-inbound INVITE route (`rtpengine_offer`) | FS 200 OK (`rtpengine_answer`) | that dialog's BYE/CANCEL/failure (`rtpengine_delete`) |
| FS ↔ WS-B | WS-B RTP↔WebRTC | FS-originated INVITE route (`rtpengine_offer`) | WS-B 200 OK (`rtpengine_answer`) | that dialog's BYE/CANCEL/failure (`rtpengine_delete`) |

- **ACK:** in-dialog, loose-routed, no rtpengine action (offer/answer already complete).
- **BYE:** `rtpengine_delete` on that dialog's session; the other dialog gets its own BYE from FS
  (B2BUA propagates the hangup).
- **CANCEL:** `rtpengine_delete` (cancel the pending offer).
- **Failure (4xx/5xx to an INVITE):** `failure_route` → `rtpengine_delete` for that leg.
- **re-INVITE (hold/codec change):** `rtpengine_manage` on that dialog's leg (update the session).

## 10. Rollback (req 8; addition 6; restart caveat)

- `#!define ROUTE_VIA_FS` in `kamailio.cfg`. **Defined** → FS-in-path; **commented** → current
  direct WS↔WS lookup (known-good).
- FS gateway/dialplan/ACL changes are **additive** — they do not affect the direct path, so flipping
  back is safe.
- **v1 toggle:** edit the define + `systemctl restart kamailio` (documented runbook). Restart is
  disruptive but acceptable for v1. **Later optimization:** runtime toggle via a Kamailio cfg
  var/htable settable with `kamcmd` (no restart) — noted, not required now.
- **Rollback verification is an acceptance check (req 7 / criterion 7):** disable `ROUTE_VIA_FS`,
  restart Kamailio, and prove direct WS-A → WS-B two-way audio still works.

## 11. Loop guard — testable invariant (addition 4)

Beyond the `X-Voice-Loop` header, the tested invariant is: **an INVITE from FS to Kamailio for
extension B is either delivered to B or fails cleanly (e.g., 404/480), and is never sent back to
FS.** A harness/log assertion confirms no FS→Kamailio→FS loop occurs (no second INVITE to FS for the
same leg; source-IP discriminator holds).

## 12. Testing plan

Extend the headless Puppeteer harness (`scratchpad/voicetest/harness.js`):
- **Answered through FS:** WS-A→WS-B with `ROUTE_VIA_FS` on → two-way audio (getStats both legs) +
  `fs_cli "show channels"`/dialplan log confirms FS executed (crit 1–3).
- **Voicemail:** WS-A calls B, B not answering → assert WS-A receives greeting media (rtpengine
  inbound to WS-A) + a stored FusionPBX voicemail recording with duration > 0; intelligibility
  confirmed by playback/listen (crit 5).
- **Clean teardown:** hangup / busy / failed-destination leave no stuck channels (`show channels`
  empty) or rtpengine sessions (crit 4).
- **Loop invariant:** assert no FS→Kamailio→FS loop (§11).
- **Regression + rollback:** existing WS↔WS audio intact; then flip `ROUTE_VIA_FS` off, restart,
  re-prove direct audio (crit 6, 7).

## 13. Risks

- **Over-broad FusionPBX dialplan rewrite** (primary): keep the gateway-route regex tight to the
  internal extension plan, ordered after feature/IVR/voicemail/conference/emergency/trunk entries,
  and log when it fires. Mitigated by narrow scope + observability + rollback flag.
- **rtpengine two-session interplay with TURN/SNAT:** validate via harness; the FS side is plain RTP
  (simpler), the complexity stays on the already-working WS legs.
- **B2BUA Call-ID separation assumption:** confirm FS issues a new Call-ID for the b-leg (expected
  for sofia gateway bridge) so the two dialogs are cleanly independent in Kamailio.
