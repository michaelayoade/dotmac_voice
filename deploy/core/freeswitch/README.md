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
