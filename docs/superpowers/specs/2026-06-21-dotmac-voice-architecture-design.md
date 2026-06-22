# DotMac Voice — E2E Architecture Design

**Date:** 2026-06-21
**Status:** Approved design. Tier 0 `dotmac_voice` skeleton + security hardening implemented & merged to `main` (2026-06-21). **Revised 2026-06-22:** CRM is the unified comms hub; sub↔voice is for the billable product only (see §1).
**Scope:** Full end-to-end reference architecture for DotMac's voice/telephony products, including UI integration with `dotmac_sub` and `dotmac_crm`. Implementation is decomposed per tier afterward (each tier gets its own implementation plan).

---

## 1. Goal & product framing

Build voice products on a **locally-hosted FusionPBX/FreeSWITCH**, as tiers on one engine (not separate stacks):

1. **Residential phone lines + Virtual PBX for organizations** — via `dotmac_sub` (billed on the existing invoice).
2. **Internal call center** — via `dotmac_crm` (extends the existing omnichannel inbox + WhatsApp-WebRTC infra).
3. **Unified customer comms (live chat + "Talk to an agent" voice)** — via `dotmac_crm` as the comms hub; the customer app reaches support **through CRM** (CRM-native chat + WebRTC voice into the support queue), with exact-customer screen-pop. (Sub is not in this path.)
4. **Later/optional:** CCaaS sold to orgs (needs a multi-tenant agent console — a separate future build), SMS gateway (separate engine), CPaaS (Jambonz).

### Locked decisions (from brainstorming)
- **Reference architecture now**, implementation decomposed per tier.
- **Agents are mixed** (in-office + remote) → a hardened public WebRTC edge is mandatory; in-office agents get a lower-RTT LAN path.
- **Scale: large with headroom** → Kamailio + RTPengine SBC edge from day one, multiple FreeSWITCH media nodes.
- **PSTN phased** → architect for full PSTN; launch on-net first (in-app talk-to-agent + internal/virtual-PBX calling). Carrier SIP trunk / DID provider is a to-be-procured **PSTN go-live dependency**, not a blocker for the on-net Tier 0 acceptance gate.
- **FusionPBX integration = Approach A**: provision via FusionPBX REST API; real-time call control + events via FreeSWITCH ESL. FusionPBX stays the admin GUI for techs.
- **Cloud↔local connectivity = Option 1**: `dotmac_voice` exposes an authenticated **public HTTPS API** (mTLS/API-key + IP allowlist to sub/crm hosts only + edge rate-limit). No VPN. Webhooks are outbound.
- **`dotmac_voice` is single-tenant**: a customer is a `customer_id`/`fusionpbx_domain` foreign key (data), not an app-level `org_id` scope. Tenancy is handled below it (FusionPBX domains) and beside it (sub accounts).
- **Comms-vs-product split (refined 2026-06-22):** `dotmac_crm` is the **unified comms hub** — ALL customer support comms (live chat, WhatsApp, voice "talk to an agent") flow through CRM, and the customer app integrates with CRM for comms. `dotmac_sub` owns the **billable voice product only** (per-customer PBX/line provisioning + per-minute/line billing) and connects to CRM for customer context + outbound notifications. **`dotmac_voice` ↔ `sub` exists ONLY for the billable product, never for support comms.** `dotmac_voice` ↔ `crm` serves the contact center + the app's talk-to-agent.

---

## 2. Components & network/media topology

Three zones: **public edge** (only internet-facing surface), **local core** (on-net, private), **cloud apps** (existing).

```
   INTERNET                          ┌─────────── PUBLIC EDGE (DMZ, public IP, on-net) ───────────────┐
 Customer native app (WebRTC)──┐     │   Kamailio  (SIP/WSS signaling SBC: registrar, ACL, rate-limit,│
 Remote agent browser (WebRTC)─┼────▶│             anti-fraud, routing, topology hiding, TLS)         │
 Carrier SIP trunk (PSTN, ph2)─┘     │   RTPengine (media relay/anchor: SRTP↔RTP, ICE)  +  coturn     │
                                     └───────────────┬────────────────────────────────────────────────┘
                                                     │ (private, LAN only)
   ┌──────────── LOCAL CORE (on-net, private) ───────┼─────────────────────────────┐
   │  FreeSWITCH (call+media engine, voicemail, IVR, mod_callcenter queues; N nodes)│
   │  FusionPBX (multi-tenant provisioning + admin GUI; on FreeSWITCH DB)           │
   │  dotmac_voice (control-plane: FusionPBX API + ESL bridge + CDR + tokens)       │
   │     └─ ESL is LOCAL-ONLY (full call control — never exposed)                   │
   └───────────────────────────────────┬───────────────────────────────────────────┘
                          authenticated public HTTPS API (mTLS/API-key + IP allowlist)
                                        │   ▲ outbound HMAC webhooks
   ┌──────────── CLOUD (existing) ──────┴───┴───────────────────────────────────────┐
   │  dotmac_sub — PRODUCT: reconcile_voice provision · CDR rating→billing · selfcare │
   │  dotmac_crm — COMMS HUB: chat/whatsapp/voice · agents · screen-pop · click-to-dial│
   │       sub ⇄ crm: customer context + outbound notifications                       │
   └─────────────────────────────────────────────────────────────────────────────────┘

   Customer app  ──comms (chat + talk-to-agent voice)──▶  dotmac_crm
                 ──account · billing · manage purchased lines──▶  dotmac_sub
```

| Component | Location | Role |
|---|---|---|
| **Kamailio** | public edge | SIP/WSS front door for ALL clients + PSTN trunk; security, routing, anti-fraud |
| **RTPengine** | public edge | media relay/anchor (public IP) — lets locally-hosted FreeSWITCH serve internet clients; SRTP↔RTP, ICE |
| **coturn** | public edge | TURN/STUN for WebRTC client ICE |
| **FreeSWITCH** | local, N nodes | call/media engine, voicemail, IVR, `mod_callcenter` queues |
| **FusionPBX** | local | multi-tenant provisioning + tech admin GUI (driven by dotmac_voice via REST) |
| **dotmac_voice** | local (beside FreeSWITCH) | control-plane: provisioning, ESL bridge, CDR, token minting, webhooks; runs local because ESL must never be public |
| **dotmac_sub** | cloud | **billable product:** provisioning intent, CDR rating→billing, selfcare to manage purchased lines. Connects to CRM for context + notifications. NOT in the support-comms path. |
| **dotmac_crm** | cloud | **unified comms hub:** live chat + WhatsApp + voice channels, agents, screen-pop, click-to-dial. The customer app's chat + talk-to-agent terminate here. |
| **Carrier SIP trunk** | external (phase 2) | PSTN origination/termination + DID inbound |

**Media paths:** PSTN ⇄ RTPengine ⇄ FreeSWITCH · remote agent/customer ⇄ RTPengine(public) ⇄ FreeSWITCH · in-office agent same path, lower RTT (edge on-net); optional LAN-direct optimization later.

---

## 3. `dotmac_voice` internals

House style: FastAPI, thin routes → services, `flush()` in services (routes commit), sync routes, Celery for background, UUID PKs, SQLAlchemy 2.0 `select()`, Pydantic v2.

```
app/
├── api/
│   ├── provisioning.py   # intent endpoints consumed by sub's reconcile_voice
│   ├── tokens.py         # mint ephemeral SIP/WebRTC creds (sub app + crm agents)
│   ├── calls.py          # click-to-dial / call control (consumed by crm)
│   ├── cdr.py            # rated-ready CDR query/feed (consumed by sub billing)
│   └── webhooks.py       # endpoint registration (crm subscribes to call events)
├── services/
│   ├── fusionpbx/        # REST client + provisioning (domains, extensions, gateways, IVR, queues)
│   ├── freeswitch/esl.py # ESL bridge: event subscribe + originate + queue control (LOCAL only)
│   ├── reconcile/voice.py# reconcile_voice(customer_id): desired vs actual → delta → apply
│   ├── tokens/           # short-lived scoped credential minting + validation hooks
│   ├── cdr/              # ingest (json_cdr/ESL) → store → rate-ready feed (store-and-forward)
│   ├── routing/          # dialplan/queue config builders, fraud policy
│   └── events/           # normalize ESL events → outbound webhooks to crm
├── tasks/
│   ├── reconcile_sweep.py   # periodic drift heal (mirrors sub's ont_verification)
│   ├── cdr_ingest.py        # store-and-forward CDRs
│   └── webhook_deliver.py   # HMAC-signed delivery w/ retry + dead-letter (crm's scheme)
├── models/   # VoiceDomain, Extension, Did, Queue, CallSession, Cdr, TokenGrant, WebhookEndpoint
└── webhooks/ # outbound delivery to crm
```

**Data model (own Postgres; `customer_id` is an FK, not a tenant scope):**
- `VoiceDomain` — `customer_id`, `fusionpbx_domain`, `sync_status`, `last_reconciled_at`
- `Extension` — domain FK, number, display, voicemail config, `sync_status`
- `Did` — E.164, route target (extension/queue/IVR), carrier gateway, status
- `Queue` — domain FK, strategy, members (internal "support" queue lives here too)
- `CallSession` — live/recent call keyed by FreeSWITCH UUID (control + correlation)
- `Cdr` — call detail + `rating_status` (raw → fed-to-sub)
- `TokenGrant` — subject, scope (e.g. `queue:support`), TTL, revoked

**Three interfaces to FreeSWITCH/FusionPBX (Approach A):**
1. **FusionPBX REST** — provisioning (slow-changing config); the "actual" source for reconcile.
2. **ESL** — real-time (events in: CREATE/ANSWER/HANGUP/CDR; commands out: originate, transfer, queue ops); local socket only.
3. **CDR feed** — `mod_json_cdr` (or ESL `CHANNEL_HANGUP_COMPLETE`) → `cdr_ingest` → `Cdr` rows.

**`reconcile_voice`** mirrors sub's ONT reconciler: read desired vs actual (FusionPBX API) → delta → apply via REST → set `sync_status`; `reconcile_sweep` heals drift; idempotent (intent carries idempotency key); circuit-breakered.

**Isolation:** `fusionpbx/` knows nothing of ESL; `cdr/` nothing of provisioning; `tokens/` independently testable. sub/crm touch only `api/` + `webhooks/`.

---

## 4. Data flows

**1. Provisioning (`reconcile_voice`)**
```
sub: customer buys voice / adds extension → records DESIRED state
 → PUT dotmac_voice /api/provisioning/domains/{customer_id} (intent, idempotency key)
   → reconcile.voice: actual (FusionPBX API) vs desired → delta → apply via REST → sync_status
     → reconcile_sweep heals drift periodically
```

**2. Inbound PSTN call (phase 2)**
```
carrier → Kamailio → FreeSWITCH dialplan → route by DID → customer IVR / queue / extension
 if call-center queue: ESL event → dotmac_voice → resolve customer_id (shared identity)
   → POST crm /api/v1/crm/inbox/webhooks/voice → screen-pop + agent_notification{kind:inbound_call}
     → agent softphone (WebRTC) rings → answer → media via RTPengine
```

**3. Customer comms via the CRM hub — live chat + "Talk to an agent" voice** (CRM is the hub; sub is NOT in this path)
```
LIVE CHAT:  customer app ──▶ CRM chat channel ──▶ agent inbox     (no voice engine involved)

TALK TO AGENT (voice):
 customer app taps "Talk to an agent" → app requests a voice token from CRM
   → CRM issues a scoped ephemeral token (queue:support, ttl ~60s; minted via dotmac_voice token API)
 → app WebRTC → Kamailio (token) → FreeSWITCH support queue
   → ESL event carries the customer id → dotmac_voice → CRM voice webhook
     → agent EXACT-customer screen-pop (sub & crm share records) → connect
```

**4. Outbound click-to-dial (agent → customer)**
```
agent clicks call in crm → POST dotmac_voice /api/calls/dial {agent_ext, destination}
 → fraud policy check (allowed route? within limits?) → ESL originate: bridge agent_ext ↔ destination
```

**5. CDR → billing**
```
FreeSWITCH emits CDR (mod_json_cdr / CHANNEL_HANGUP_COMPLETE)
 → dotmac_voice cdr_ingest → Cdr rows (rating_status=raw)
   → sub PULLS rated-ready CDRs via dotmac_voice /api/cdr (store-and-forward; never lose billable records)
     → sub cdr_rating → UsageCharge (staged→posted) → InvoiceLine (LedgerCategory.voice_service)
```

**Deliberate choices:** identity rides on the call (subscriber_id as ESL channel var / SIP header) → exact screen-pop for in-app calls, best-effort phone match for raw PSTN. CDRs are store-and-forward and *pulled* by sub → no lost revenue if sub is briefly unreachable.

---

## 5. UI integration

### A) sub selfcare "Phone" tab (web — Jinja+HTMX)
- Nav: `<a href="/portal/phone">` in `templates/layouts/customer.html` (~L107), `active_page="phone"`.
- Route: `@router.get("/phone")` in `app/web/customer/routes.py` → a `web_voice` context builder.
- Template: `templates/customer/phone/` using house macros (`status_badge`, `empty_state`, `live_search`), CSRF on POST, dark-mode pairs.
- Customer does: manage the lines/PBX they **purchased** — extensions/DIDs, call forwarding, voicemail-to-email, IVR/business-hours (virtual-PBX tier), view call history + usage/charges. **Contacting support is NOT here** — that's the comms app via CRM (§5B).
- Writes: HTMX POST → sub service → `reconcile_voice` intent → dotmac_voice. Reads via sub's voice context builder → dotmac_voice.

### B) Customer comms app — live chat + "Talk to an agent" (via CRM, not sub)
- The customer app integrates with **CRM** for ALL support comms: live chat (CRM-native channel) and "Talk to an agent" voice. Sub is not in this path.
- Voice backend contract (stack-agnostic): button → **CRM** issues a scoped ephemeral token (`queue:support`, ~60s; minted via dotmac_voice `/api/tokens`) → app opens WebRTC/SIP to Kamailio (WSS) → support queue; in-call UI = mute/hangup/connecting. Identity flows on the call for exact screen-pop (sub & crm share records).
- **Native-app finding (surveyed 2026-06-21):** the only native app is `dotmac_field` — a **field-technician/vendor app** (Flutter, Riverpod + Dio + go_router, talks to `crm.dotmac.io`, zero VoIP today). **There is NO customer-facing mobile app**; `dotmac_sub` is web-only. So the customer "Talk to an agent" target is an **open Tier-2 decision**: (1) browser WebRTC in sub's web Phone tab (works today, no app); (2) build a new customer Flutter app (separate project); or (3) the feature is actually field-tech→dispatch calling, in which case `dotmac_field` is the home (`lib/core/sip/` + a `support_call` feature; recommend `flutter_webrtc` + `sip_ua`; needs mic permissions + iOS audio session; FCM/CallKit only if inbound). Backend token contract is unchanged regardless.

### C) crm agent softphone + voice channel (web — reuses WhatsApp-calling infra)
- Voice channel: add `voice` to `ChannelType` (`app/models/crm/enums.py`) + enum migration; routing rules already parameterize on channel type.
- Inbound/screen-pop: new `POST /api/v1/crm/inbox/webhooks/voice` + `VoiceHandler` → resolve via existing `person_identity` (exact, via subscriber_id) → `broadcast_agent_notification(..., {kind:"inbound_call", subscriber_id})` (WS path already wired: `broadcaster.py` → Redis → `inbox-websocket.js`).
- Softphone widget: new `static/js/softphone.js` (Alpine) in `templates/admin/crm/inbox.html`, reusing coturn + WhatsApp WebRTC-config pattern, registers to Kamailio with an agent ephemeral token.
- Click-to-dial: phone icon → HTMX POST → crm → dotmac_voice `/api/calls/dial`; new `freeswitch` connector type.
- Logging: each call = a `Message` (channel `voice`, metadata: duration/disposition/recording) on the customer's conversation.
- Add index on `PersonChannel.address` for call-center inbound scale.

**Payoff:** shared customer records + identity-on-call → agent screen-pop for in-app calls opens the exact subscriber with full context.

---

## 6. Security & fraud

- **Ephemeral scoped tokens** everywhere (short TTL, single scope, revocable); no permanent SIP creds to clients.
- **Customer in-app calls caged** to `queue:support` — cannot dial PSTN/arbitrary numbers (zero customer-origination toll-fraud surface).
- **Outbound PSTN guards** (agents / virtual-PBX, phase 2): per-customer dial policy, international/premium limits, **balance/credit check before expensive routes**, velocity anomaly detection + alerting, Kamailio rate limits.
- **Kamailio SBC hardening**: registration throttling, source ACLs, fail2ban-style banning, topology hiding, TLS signaling + SRTP media.
- **ESL never public**; dotmac_voice ↔ FreeSWITCH local-only.
- **API ingress**: mTLS/API-key + IP allowlist (sub/crm only) + edge rate limit. **Webhooks**: HMAC-signed. **Secrets**: OpenBao. Stored creds via `credential_crypto`.
- **Call recording** (if enabled): consent + regulatory handling, encrypted at rest, access-controlled.

---

## 7. Error handling & resilience

- **Self-healing provisioning:** `reconcile_voice` idempotent (idempotency key) + `sync_status` + `reconcile_sweep`; FusionPBX API + ESL circuit-breakered (like OLT writes).
- **Never lose billable records:** CDRs store-and-forward with retry + dead-letter; sub pulls; reconcile on recovery.
- **Webhook delivery:** retries w/ backoff + dead-letter (crm pattern).
- **Graceful degradation:** token failure → UI fallback ("call this number"/retry); ingress down → provisioning queues + reconciles later; **live calls unaffected** (media path independent of cloud apps).
- **HA:** Kamailio active/standby (or active-active), RTPengine redundancy, FreeSWITCH N nodes (dead node drops its in-flight calls, new calls route to survivors); ESL auto-reconnect.

---

## 8. Testing

- **Unit:** services on SQLite in-memory; mock FusionPBX API + ESL.
- **Integration:** dockerized FreeSWITCH + FusionPBX test instance; run `reconcile_voice` against it; assert ESL events.
- **E2E:** SIPp / headless-WebRTC client places a call through Kamailio → FreeSWITCH; assert chain: ESL event → crm voice webhook → screen-pop notification → CDR recorded.
- **Contract tests:** sub↔dotmac_voice and crm↔dotmac_voice APIs (prevent cross-repo drift).
- **Fraud tests (must-pass):** customer token cannot dial PSTN; international/velocity limits enforced.
- **Discipline:** validate on test extensions, never live customers; load-test the Kamailio/RTPengine edge to the "large headroom" target before launch.

---

## 9. Implementation decomposition (per-tier plans, written separately)

- **Tier 0 — Core & edge:** local FreeSWITCH + FusionPBX; Kamailio + RTPengine + coturn public edge; dotmac_voice skeleton (ESL bridge, FusionPBX client, authenticated ingress); one real on-net call; fraud baseline. *Dependency: public edge/DMZ for on-net Tier 0; carrier SIP trunk + DID only for the PSTN slice.*
- **Tier 1 — Lines + Virtual PBX + billing:** voice service type in sub; `reconcile_voice`; CDR rating → invoice; sub selfcare Phone tab.
- **Tier 2 — Contact center + customer comms (CRM hub):** crm voice channel + agent softphone + click-to-dial; customer comms app (live chat + talk-to-agent) as a **CRM client**; sub↔crm context + notification routing. Sub is not in the comms path.
- **Later:** CCaaS (multi-tenant agent console), SMS gateway, CPaaS.

## 10. Open items / dependencies

- Carrier SIP trunk / DID provider (gates PSTN; on-net launch doesn't need it).
- **Customer comms app (Tier-2 decision):** no customer mobile app exists yet (only `dotmac_field`, agent-facing). The comms app (live chat + talk-to-agent) is a **CRM client**. Choose: browser-based comms in CRM/web selfcare / new customer Flutter app. (Field-tech→dispatch via `dotmac_field` is a separate internal case.) See §5B.
- **Splynx sync cleanup in dotmac_sub** — stale `splynx_sync` Celery beat tasks point at the decommissioned host; confirm disabled before layering voice billing on the same `account_id`.
- Public-IP / DMZ provisioning at the local hosting site for the edge.
- Secure ingress endpoint (`voice.dotmac.io` or similar) with mTLS/API-key + IP allowlist.
