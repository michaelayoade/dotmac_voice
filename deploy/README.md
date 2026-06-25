# DotMac Voice — deployment configs (EDGE Kamailio + CORE FreeSWITCH)

Source-of-truth copies of the live infra config. The running systems:
- **EDGE** `10.120.120.50` (internal `10.10.10.1`): Kamailio (WebRTC registrar/edge), rtpengine, coturn, nginx.
- **CORE** `10.10.10.10`: FreeSWITCH/FusionPBX (PBX/media), dotmac_voice app.

## edge/kamailio.cfg
Kamailio config. Deploy: `cat > /etc/kamailio/kamailio.cfg.dmnew` then `kamailio -c -f .dmnew` (must pass) then `mv` + `systemctl restart kamailio`.

## FS-in-path rollback (ROUTE_VIA_FS)
- **Defined** in kamailio.cfg → calls route through FreeSWITCH (PBX features).
- **Commented** → direct WS↔WS (known-good baseline; two-way audio with no FS in path).
- Toggle: edit the `#!define ROUTE_VIA_FS` line, config-check, `systemctl restart kamailio`, then verify with `scratchpad/voicetest/harness.js` (RELAY=0).
- Pre-change backup on EDGE: `/etc/kamailio/kamailio.cfg.pre-fsinpath`.
