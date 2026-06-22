# Production Deploy Runbook — 2-VM Proxmox (EDGE + CORE)

**Date:** 2026-06-22
**Topology:** Two VMs on one Proxmox host. **EDGE** (public IP, DMZ — Kamailio + RTPengine + coturn + API reverse-proxy) and **CORE** (private — FreeSWITCH + FusionPBX + dotmac_voice + Postgres). EDGE↔CORE talk over an internal Proxmox bridge (`vmbr1`) — no WAN, no WireGuard. ESL is bound to `127.0.0.1` on CORE and never leaves it.
**Execution:** Ops runbook — you run these on Proxmox / the VMs; each task ends with a **verification gate**. "Test" = a process is up / a call completes, not pytest.
**Relationship to other docs:** This is the concrete, Proxmox-specific realization of Tier 0 Part 1 in `2026-06-21-tier-0-core-and-edge.md`. The `dotmac_voice` app (Part 2) is already built + merged on `main`.

> **Validation note:** standing up real FreeSWITCH + FusionPBX on CORE is *also* how we validate the mock-built `dotmac_voice` FusionPBX client. Treat Task 8's "validate provisioning against real FusionPBX" as a gate — if the REST assumptions are wrong, the client gets fixed before go-live.

---

## 0. Fill these in first (used as placeholders below)

```
EDGE_PUBLIC_IP   = <a public IP from your AS328160 pool>
EDGE_GW          = <public gateway>
CORE_PRIV_IP     = 10.10.10.10        # CORE on internal bridge (suggested)
EDGE_PRIV_IP     = 10.10.10.1         # EDGE on internal bridge
INTERNAL_CIDR    = 10.10.10.0/24
SIP_FQDN         = sip.dotmac.io      # → EDGE_PUBLIC_IP
VOICE_API_FQDN   = voice.dotmac.io    # → EDGE_PUBLIC_IP
ON_NET_SUBNETS   = <your subscriber/agent CIDRs that may reach CORE directly>
```
OS for both VMs: **Debian 12 (bookworm)**. DNS: point `SIP_FQDN` and `VOICE_API_FQDN` A-records at `EDGE_PUBLIC_IP` now (cert issuance needs them).

---

## 1. Proxmox networking

**Deliverable:** a public bridge for EDGE and an internal-only bridge for EDGE↔CORE.

- [ ] **vmbr0 (public):** your existing public bridge (uplink to your AS). EDGE's first NIC attaches here, gets `EDGE_PUBLIC_IP`.
- [ ] **vmbr1 (internal):** create an isolated bridge with **no physical port** (host-internal only). On the Proxmox host `/etc/network/interfaces`:
  ```
  auto vmbr1
  iface vmbr1 inet static
      address 10.10.10.254/24
      bridge-ports none
      bridge-stp off
      bridge-fd 0
  ```
  `ifreload -a` (or `systemctl restart networking`).
- [ ] **Verify:** `ip a show vmbr1` shows `10.10.10.254/24`; `brctl show vmbr1` lists no physical port.

## 2. Create the two VMs

**Deliverable:** EDGE and CORE VMs running Debian 12.

- [ ] **CORE VM** — **4–8 vCPU, 8–16 GB RAM, 60+ GB SSD**. Single NIC on **vmbr1** (`CORE_PRIV_IP`, gw `EDGE_PRIV_IP` for egress via EDGE, or a separate NAT — see note). CPU type `host`; **do not over-commit** this host's CPU (FreeSWITCH is real-time/jitter-sensitive — pin vCPUs if possible, avoid noisy neighbors).
- [ ] **EDGE VM** — **2–4 vCPU, 4 GB RAM, 25 GB SSD**. NIC1 on **vmbr0** (`EDGE_PUBLIC_IP`), NIC2 on **vmbr1** (`EDGE_PRIV_IP`).
- [ ] **Egress for CORE:** CORE has no public IP. Give it internet egress for package installs either via (a) EDGE doing NAT/forwarding for `INTERNAL_CIDR`, or (b) a temporary second NIC on a NAT bridge during install, removed after. Document which you chose.
- [ ] **Verify:** from EDGE, `ping CORE_PRIV_IP` works; from CORE, `ping EDGE_PRIV_IP` works; CORE can `apt-get update`.

## 3. CORE — FreeSWITCH

**Deliverable:** FreeSWITCH running, `fs_cli` connects, internal profile RUNNING. ESL local-only.

- [ ] Install FreeSWITCH (SignalWire repo per current docs, or distro pkg). `apt-get install -y freeswitch-meta-all` (or the SignalWire token repo).
- [ ] `event_socket.conf.xml`: `listen-ip` = `127.0.0.1`, strong `password` (record → `ESL_PASSWORD`).
- [ ] Bind SIP profiles to `CORE_PRIV_IP` (internal profile listens for Kamailio on vmbr1); set RTP range `16384-32768`.
- [ ] `systemctl enable --now freeswitch`.
- [ ] **Verify:** `fs_cli -x 'status'` (uptime); `fs_cli -x 'sofia status'` (internal RUNNING); `ss -tlnp | grep 8021` shows ESL on `127.0.0.1` ONLY.

## 4. CORE — FusionPBX + Postgres

**Deliverable:** FusionPBX UI reachable on vmbr1 only; a test domain + two extensions registering against CORE.

- [ ] Run the official FusionPBX installer (PostgreSQL + nginx + php-fpm). **Bind FusionPBX nginx to `CORE_PRIV_IP`** (never public).
- [ ] Create domain `test.local`; extensions `1001`, `1002` with passwords.
- [ ] Record the FusionPBX provisioning interface for `dotmac_voice` (→ `FUSIONPBX_API_URL`, `FUSIONPBX_API_KEY`). **This is the validation point** — confirm how provisioning actually works (REST app? DB? command). If it's not the REST shape the client assumes, that's the fix-before-go-live finding.
- [ ] **Verify:** register a softphone as `1001@test.local` against `CORE_PRIV_IP` from a vmbr1/on-net host; `fs_cli -x 'sofia status profile internal reg'` shows it; call `1002` — two-way audio.

## 5. CORE — dotmac_voice (Docker Compose)

**Deliverable:** `dotmac_voice` running on CORE, talking to local FusionPBX + ESL.

- [ ] Install Docker + compose on CORE. Clone/copy `dotmac_voice` (from your repo).
- [ ] `.env`: `FUSIONPBX_API_URL=http://127.0.0.1:<fpbx>`, `FUSIONPBX_API_KEY=...`, `ESL_HOST=127.0.0.1`, `ESL_PORT=8021`, `ESL_PASSWORD=...`, `EDGE_WSS_URL=wss://${SIP_FQDN}:443`, `VOICE_INGRESS_API_KEYS=<key(s) for sub/crm>`, `VOICE_INGRESS_ALLOWED_IPS=<sub host>,<crm host>,${EDGE_PRIV_IP}`, strong `TOKEN_SIGNING_KEY`, real `SECRET_KEY`. **Mirror none of these to the public box.**
- [ ] `docker compose up -d`; `alembic upgrade head` runs on boot.
- [ ] Wire the ESL→dispatch consumer (the live-cutover gap: install a handler on `EslBridge.on_event` → `dispatch_and_enqueue`) and verify greenswitch `get_response()` for `bgapi`.
- [ ] **Verify:** `curl http://127.0.0.1:8001/health` ok; `curl -H 'X-API-Key: <key>' http://127.0.0.1:8001/api/cdr` returns `[]` (ingress works); a provisioning PUT reconciles a domain into the real FusionPBX (validates the client).

## 6. EDGE — Kamailio + RTPengine + coturn

**Deliverable:** public SBC that proxies SIP/WSS to CORE and relays media; TURN reachable.

- [ ] **RTPengine:** `interface=` set to BOTH `EDGE_PUBLIC_IP` (for internet media) and `EDGE_PRIV_IP` (toward CORE); media range `30000-40000`. `systemctl enable --now ngcp-rtpengine-daemon`.
- [ ] **Kamailio:** `apt-get install -y kamailio kamailio-tls-modules kamailio-websocket-modules kamailio-json-modules`. Config: WSS listener on `tls:EDGE_PUBLIC_IP:443`; route `REGISTER`/`INVITE` to FreeSWITCH at `CORE_PRIV_IP:5060` over vmbr1; offer/answer via RTPengine (WebRTC↔SIP, SRTP↔RTP); enable `pike` flood-protection + source ACLs + topology hiding.
- [ ] **coturn:** `listening-ip=EDGE_PUBLIC_IP`, `realm=dotmac.io`, `use-auth-secret`, a `static-auth-secret` (record → dotmac_voice mints TURN creds), TLS on `5349`.
- [ ] **TLS cert** for `SIP_FQDN` (Let's Encrypt / your CA) — WSS clients reject self-signed.
- [ ] `systemctl enable --now kamailio coturn`.
- [ ] **Verify:** `kamcmd core.info` + `rtpengine-ctl list` healthy; a `trickle-ice` test against `turn:${SIP_FQDN}:3478` returns a `relay` candidate.

## 7. EDGE — API reverse proxy (voice.dotmac.io → CORE)

**Deliverable:** the `dotmac_voice` API reachable by sub/crm via the public edge, proxied over vmbr1 to CORE.

- [ ] nginx/caddy on EDGE: `https://${VOICE_API_FQDN}` (real cert) → proxy_pass to `http://${CORE_PRIV_IP}:8001`. Enforce **mTLS or API-key + IP allowlist** (sub/crm hosts only) at the proxy; rate-limit.
- [ ] **Verify:** from a sub/crm host, `curl -H 'X-API-Key: <key>' https://${VOICE_API_FQDN}/api/cdr` → `[]`; from a non-allowlisted IP → 401/403.

## 8. MILESTONE — one real WebRTC call through the public edge

**Deliverable (Tier-0 prod acceptance gate):** an external browser WebRTC softphone registers via `wss://${SIP_FQDN}` and completes a `1001↔1002` call with media relayed by RTPengine, FreeSWITCH bridging on CORE.

- [ ] Serve a SIP.js test page (WSS `${SIP_FQDN}`, coturn ICE, `1001@test.local`). From OUTSIDE your network, register + call `1002`.
- [ ] **Verify:** two-way audio; `rtpengine-ctl list` shows the session; `fs_cli -x 'show channels'` shows the bridge; `kamcmd ul.dump` shows the registration. **Do not go live until this passes.**

## 9. Firewall matrix

| Host | Allow inbound | From | Notes |
|---|---|---|---|
| **EDGE** | 443/tcp (WSS+API) | internet | the only broadly-public ports |
| **EDGE** | 5060-5061/udp+tcp (SIP) | internet + carrier | tighten to carrier IP for the trunk |
| **EDGE** | 3478/udp+tcp, 5349/tcp (TURN) | internet | coturn |
| **EDGE** | 30000-40000/udp (media) | internet | RTPengine |
| **CORE** | 5060/udp (FS SIP) | `EDGE_PRIV_IP` only | vmbr1 |
| **CORE** | 16384-32768/udp (RTP) | `EDGE_PRIV_IP` only | vmbr1 |
| **CORE** | 8001/tcp (API) | `EDGE_PRIV_IP` only | proxied from EDGE |
| **CORE** | FusionPBX nginx, ESL 8021 | **localhost / on-net mgmt only** | NEVER from EDGE/public |

On-net subscribers may reach CORE's registrar directly (add `ON_NET_SUBNETS` to CORE's SIP allow) to keep on-net media fully local.

## 10. Ops: monitoring, backups, rollout

- [ ] **Monitoring:** ship FreeSWITCH/Kamailio/dotmac_voice metrics + logs to **dotmac-observe** (Grafana/VictoriaMetrics/Zabbix). Add **Homer** (SIP capture) on EDGE for call-failure debugging.
- [ ] **Backups:** CORE Postgres (FusionPBX + dotmac_voice) under your existing DB backup + replication discipline (per the DB replication runbook). Never lose CDRs.
- [ ] **Fraud:** confirm the dial fraud-policy + token caging are enforced before any customer/PSTN dialing; international dial limits + balance checks on for PSTN.
- [ ] **Rollout:** internal call center → pilot customers → GA.

## 11. HA / later
- Single Proxmox host = SPOF. When uptime matters: add a 2nd Proxmox host (cluster), split EDGE/CORE across hosts; or use remote **`149.102` as a 2nd geo-edge** (Kamailio active/standby) and the **PSTN trunk termination point** (carriers prefer a DC public IP).

## Open dependencies
- Carrier SIP trunk / DID (PSTN only; on-net launch doesn't need it).
- `dotmac_voice` FusionPBX-client validation against the real FusionPBX (Task 4/5 gate).
- ESL→dispatch consumer wiring + greenswitch `get_response()` confirmation (Task 5).
- mTLS material for the API ingress (Task 7).
