# ARCHITECTURE.md
# Website-Specific Real-Time Remote Selenium System
# Version: 1.0 — Phase 0 Contract
# Last updated: Phase 0 (no implementation code yet)

---

## Table of Contents

1. [Project Purpose and Scope](#1-project-purpose-and-scope)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Components and Responsibilities](#3-components-and-responsibilities)
4. [Transport Layer: WebSocket-Only](#4-transport-layer-websocket-only)
5. [Message Protocol](#5-message-protocol)
6. [worker_id — The Routing Key](#6-worker_id--the-routing-key)
7. [Multi-Worker Support](#7-multi-worker-support)
8. [DOM Versioning (per Worker)](#8-dom-versioning-per-worker)
9. [Worker Authority Model](#9-worker-authority-model)
10. [Controller Synchronization Model](#10-controller-synchronization-model)
11. [BeautifulSoup — HTML Normalization](#11-beautifulsoup--html-normalization)
12. [MutationObserver — Change Detection](#12-mutationobserver--change-detection)
13. [Zstandard Compression](#13-zstandard-compression)
14. [CSS / Resource Caching](#14-css--resource-caching)
15. [Command Queue (per Worker)](#15-command-queue-per-worker)
16. [Error Recovery (per Worker, isolated)](#16-error-recovery-per-worker-isolated)
17. [Security Model](#17-security-model)
18. [Render Deployment](#18-render-deployment)
19. [Project File Structure](#19-project-file-structure)
20. [Sequence Diagrams](#20-sequence-diagrams)

---

## 1. Project Purpose and Scope

This system enables **real-time remote interaction with one specific, pre-authorized website**
through one or more headless Chrome browsers running on remote Worker machines.

**This system is NOT:**

- A generic remote browser
- VNC, AnyDesk, or any remote desktop streaming solution
- A screenshot-streaming system
- An arbitrary code execution platform
- A general-purpose browser automation framework

**This system IS:**

- A website-specific Selenium automation relay
- A DOM-synchronization system (incremental diffs, not full-page polls)
- A multi-Worker coordination layer (each Worker independent, never sharing state)

---

## 2. High-Level Architecture

```
                         INTERNET

┌──────────────────────────────────┐
│           CONTROLLER              │
│     PySide6 GUI Application       │
│  (tracks state per worker_id)     │
└────────────────┬─────────────────┘
                 │
                 │  WebSocket / WSS
                 │  ← ONLY real-time transport →
                 │  (no HTTP polling, ever)
                 ▼
┌──────────────────────────────────┐
│         FASTAPI SERVER            │
│           (Render)                │
│                                   │
│      *** RELAY ONLY ***           │
│  Routes strictly by worker_id.    │
│  Never runs Chrome, Selenium,     │
│  or HTML parsing. Never stores    │
│  HTML snapshots permanently.      │
│  Never exposes an HTTP polling    │
│  endpoint for real-time data.     │
└───────┬──────────┬────────┬──────┘
        │          │        │
        │ WS/WSS   │        │ WS/WSS
        ▼          ▼        ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ WORKER-01│ │ WORKER-02│ │ WORKER-N │
│ Selenium │ │ Selenium │ │ Selenium │
│ +Chrome  │ │ +Chrome  │ │ +Chrome  │
│          │ │          │ │          │
│ DOM v102 │ │ DOM v45  │ │ DOM v...  │
│ (own     │ │ (own     │ │ (own     │
│  state)  │  state)  │ │  state)  │
└────┬─────┘ └────┬─────┘ └────┬─────┘
     ▼             ▼            ▼
  TARGET        TARGET       TARGET
  WEBSITE       WEBSITE      WEBSITE
```

Each Worker is an independent process with its own Chrome session. Workers never
communicate with each other. The server routes every Worker-scoped message using
`worker_id` as the sole routing key.

---

## 3. Components and Responsibilities

### 3.1 FastAPI Server (`server/`)

**Is:** A relay and authentication gate.

**Is not:** A browser, an HTML parser, a DOM state store, or a polling endpoint.

| Responsibility | Detail |
|---|---|
| WebSocket endpoints | `/ws/worker` and `/ws/controller` — WSS only |
| Authentication | Token-based, tokens from env vars only (`WORKER_TOKEN`, `CONTROLLER_TOKEN`) |
| Worker registration | Registers each connecting Worker by its `worker_id`; maintains `worker_id → WS connection` map |
| Controller registration | Registers each Controller; tracks `Controller → subscribed worker_id` |
| Message routing | COMMAND → correct Worker only; COMMAND_RESULT / FULL_SNAPSHOT / DOM_UPDATE → subscribed Controllers only |
| Disconnect isolation | One Worker or Controller disconnect does NOT affect any other Worker's or Controller's entries |
| Reconnect support | Re-registrations accepted; old entry replaced cleanly |
| Status tracking | Per-Worker status (connected/disconnected/unknown) |
| Health endpoint | Only what Render requires for its health check (GET /health returning 200 OK) — never a polling endpoint for real-time state |

**Routing table (internal, at minimum):**

```
worker_id  →  Worker WebSocket connection       (one-to-one)
worker_id  →  set of subscribed Controller WSs  (one-to-many)
```

**Must never:**
- Broadcast a Worker-scoped command to every connected Worker
- Permanently store every HTML snapshot
- Run Chrome, Selenium, or BeautifulSoup
- Expose an HTTP polling endpoint as an alternative to WebSocket for status, commands, or DOM state

---

### 3.2 Worker (`worker/`)

One independent OS process per Worker instance. Multiple Workers run simultaneously.

| Responsibility | Detail |
|---|---|
| Unique identity | Stable `worker_id` established at startup from env/config; persisted across reconnects |
| Chrome management | Start, maintain, crash-detect, restart — one persistent session per Worker |
| Selenium commands | navigate, click, type, scroll, etc. — explicit waits, no bare `time.sleep()` |
| MutationObserver | Injected JS to detect DOM changes; batched/debounced (50–200 ms) |
| HTML acquisition | `driver.page_source` — Python Unicode str, not re-encoded |
| HTML normalization | BeautifulSoup + `website/parser.py` |
| DOM versioning | Independent monotonic version counter per Worker instance |
| DOM diffing | Compare current vs. prior normalized HTML; produce structured diff ops |
| Compression | Zstandard for large payloads; skip for small messages |
| WebSocket client | Outbound WSS to server only; never accepts incoming WS connections |
| Message tagging | Every outgoing message includes this instance's `worker_id` |
| Reconnection | Self-contained; zero effect on other Workers |

**Must never:**
- Share state with another Worker instance
- Execute commands whose `worker_id` doesn't match its own (log and discard — server should never route incorrectly, but Worker defends itself)
- Implement cross-Worker coordination logic

---

### 3.3 Controller (`controller/`)

PySide6 GUI that remote-controls the website through Workers.

| Responsibility | Detail |
|---|---|
| Worker list display | Shows ALL known Workers simultaneously (worker_id, status, DOM version) |
| Worker switching | User selects active Worker; Controller tracks each separately |
| DOM state tracking | Independent `current_version`, `is_stale` flag, and DOM tree per `worker_id` |
| Command dispatch | Always tagged with the currently selected `worker_id` |
| Version checking | Validates `base_version` per `worker_id`; requests RESYNC on mismatch |
| Resync policy | After reconnect: always treat every tracked Worker's state as stale; request FULL_SNAPSHOT |
| Statistics | Per-Worker bandwidth, DOM version, counts |
| Screenshots | On-demand only for selected Worker; not streamed |

**Never performs website actions locally** — all actions route through the server to the correct Worker.

---

### 3.4 Shared Layer (`shared/`)

The sole source of truth for the message protocol.

| File | Responsibility |
|---|---|
| `shared/protocol.py` | Message type constants, `protocol_version`, `WORKER_SCOPED_TYPES` list, command allowlist |
| `shared/messages.py` | Construction helpers, validation, idempotency check — prevents ad hoc dicts anywhere |
| `shared/models.py` | Pydantic (or dataclass) models for every message type; `worker_id` required on Worker-scoped types |

No module outside `shared/` constructs messages as raw dicts. This rule applies to ALL modules.

---

### 3.5 Website Layer (`website/`)

Website-specific knowledge, shared read-only by every Worker instance.

| File | Responsibility |
|---|---|
| `website/config.py` | `TARGET_DOMAIN`, timeouts, and other site config |
| `website/selectors.py` | CSS/XPath selectors for the target site |
| `website/pages.py` | Page-specific rules and URL patterns |
| `website/commands.py` | Website-specific high-level commands built on Selenium primitives |
| `website/parser.py` | BeautifulSoup normalization pipeline; Unicode-robust |
| `website/rules.py` | Business logic specific to the target site |

These files define no `worker_id` logic — they are stateless utilities used by each Worker independently.

---

## 4. Transport Layer: WebSocket-Only

> **HTTP polling is not used anywhere in this system — not as a primary mechanism,
> not as a fallback, and not as a debugging endpoint for real-time data.**

All real-time communication uses WebSocket (WS in development, WSS in production):

- Worker → Server: `wss://<server>/ws/worker`
- Controller → Server: `wss://<server>/ws/controller`

The only HTTP surface exposed by the server is:
- `GET /health` — required by Render for process health checks (returns 200 OK, no real-time state)
- The WS upgrade handshake itself (HTTP 101 Switching Protocols — not polling)

If a future phase appears to need a polling fallback, **stop and flag it** — it violates
this architecture and must be solved another way.

---

## 5. Message Protocol

### 5.1 Protocol Version

| Field | Value |
|---|---|
| `protocol_version` | `1` (starts here; bump only when explicitly specified, in lockstep across all modules) |

A receiver that encounters an unrecognized `protocol_version` **must reject the message
and log the mismatch** — never attempt best-effort parsing.

A `protocol_version` bump requires simultaneous updates in server, worker, controller, and
shared modules within the same development phase.

### 5.2 Universal Fields (every message, no exceptions)

| Field | Type | Description |
|---|---|---|
| `type` | `string` | One of the message type constants (see §5.3) |
| `message_id` | `UUID (string)` | Generated by sender; receiver must deduplicate — do not apply a repeated `message_id` twice |
| `timestamp` | `string` | ISO-8601 UTC (`2025-01-01T12:00:00.000Z`); **never used for sync ordering** — clock skew between nodes is assumed |
| `protocol_version` | `int` | Must be `1` currently |

### 5.3 Message Types and Required Fields

`*` = required on Worker-scoped types (see §6).

| Message Type | Direction | worker_id* | Additional Fields |
|---|---|---|---|
| `HELLO` | any → any | — | `role` (`"worker"` \| `"controller"`) |
| `AUTH` | any → server | — | `token` |
| `WORKER_REGISTER` | worker → server | ✅ required | `worker_id` (self), `capabilities` (empty dict `{}`, currently unused due to query param fast-path) |
| `CONTROLLER_REGISTER` | controller → server | — | — |
| `WORKER_STATUS` | worker → server → controller | ✅ required | `status` (`connected`\|`disconnected`\|`crashed`), `dom_version` |
| `COMMAND` | controller → server → worker | ✅ required | `command` (from allowlist), `payload` (dict) |
| `COMMAND_RESULT` | worker → server → controller | ✅ required | `command`, `success` (bool), `error` (str\|null) |
| `FULL_SNAPSHOT` | worker → server → controller | ✅ required | `version` (int), `url`, `title`, `html` (str, possibly compressed) |
| `DOM_UPDATE` | worker → server → controller | ✅ required | `base_version` (int), `version` (int), `ops` (list of diff ops), `compressed` (bool) |
| `RESYNC_REQUEST` | controller → server → worker | ✅ required | — |
| `ERROR` | any → any | ✅ if Worker-scoped | `code`, `detail` |
| `PING` | any → any | — | — |
| `PONG` | any → any | — | — |

### 5.4 DOM Diff Operations

`DOM_UPDATE.ops` is a list of structured diff operation objects:

| Op | Fields | Meaning |
|---|---|---|
| `ADD` | `selector`, `position`, `html` | Insert new element |
| `REMOVE` | `selector` | Remove element |
| `REPLACE` | `selector`, `html` | Replace element subtree |
| `TEXT` | `selector`, `text` | Update text content only |
| `ATTRIBUTE` | `selector`, `attr`, `value` | Update one attribute |
| `VALUE` | `selector`, `value` | Update form field value |

### 5.5 Command Allowlist

Only these commands may appear in a `COMMAND` message. The server enforces this at the
routing layer; the Worker enforces it again at execution. If a command is not on this list,
the server rejects it with an `ERROR` and does not forward it.

```
navigate
click
type
clear
keypress
scroll
back
forward
refresh
screenshot
page_source
```

Adding a command requires an explicit phase decision that updates both the server allowlist
and the Worker's command handler simultaneously.

**Never implement:** `execute_shell`, `execute_python`, `execute_arbitrary_javascript`

### 5.6 Message Construction Rule

> No module anywhere in the project constructs messages as ad hoc Python dicts.
> All message construction goes through `shared/messages.py` helpers.
> The helpers make it **impossible** to construct a Worker-scoped message without
> supplying `worker_id` — this is compile-time-equivalent enforcement in Python.

---

## 6. worker_id — The Routing Key

`worker_id` is the single source of truth for all routing decisions.

### 6.1 Assignment

- Each Worker generates or reads its `worker_id` at startup (from `WORKER_ID` env var or config).
- The `worker_id` must be **stable across reconnects** for the same physical Worker.
  It does not change when the WebSocket drops and re-establishes.
- Suggested format: `worker-01`, `worker-02`, ... or a UUID-based slug. Format is fixed at deployment time.

### 6.2 Routing Rules

| Message received by server | Routing action |
|---|---|
| `COMMAND` (from Controller, `worker_id=X`) | Forward to Worker X's WS only; error if Worker X not connected |
| `COMMAND_RESULT` (from Worker X) | Forward to all Controllers subscribed to Worker X only |
| `FULL_SNAPSHOT` (from Worker X) | Forward to all Controllers subscribed to Worker X only |
| `DOM_UPDATE` (from Worker X) | Forward to all Controllers subscribed to Worker X only |
| `RESYNC_REQUEST` (from Controller, `worker_id=X`) | Forward to Worker X's WS only |
| `WORKER_STATUS` (generated by server on disconnect of Worker X) | Send to Controllers subscribed to Worker X only |

**A message for Worker A must never reach Worker B. A message from Worker B must never
reach a Controller subscribed only to Worker A.**

### 6.3 Invalid worker_id Handling

If the server receives a Worker-scoped message whose `worker_id` does not correspond to
a currently registered, connected Worker, the server **must** respond with an `ERROR`
message. It must never silently drop the message or forward it anyway.

---

## 7. Multi-Worker Support

### 7.1 Isolation Guarantees

Each Worker has fully independent:

| Resource | Isolation |
|---|---|
| WebSocket connection | One TCP connection per Worker; disconnect of one has no effect on others |
| Chrome / Selenium session | Separate OS process; no shared browser state |
| DOM state | Independent Python state object; keyed by `worker_id` in any in-memory store |
| DOM version counter | Starts at 0 (or 1) independently; increments independently |
| Command queue | Sequential per Worker; queues for different Workers run concurrently |
| Resource cache | Hash cache per Worker's session |
| Reconnect lifecycle | Self-contained per Worker |
| Crash recovery | Self-contained per Worker |

No Worker instance contains any reference to another Worker's state.
The server's routing table enforces this at the message layer.

### 7.2 Server Routing Table (implementation contract)

```python
# Conceptual representation — actual implementation lives in
# server/worker_manager.py and server/controller_manager.py

worker_connections: dict[str, WebSocket]
# worker_id → currently connected Worker WebSocket

worker_subscribers: dict[str, set[WebSocket]]
# worker_id → set of Controller WebSockets subscribed to this Worker

controller_subscription: dict[WebSocket, str | None]
# Controller WebSocket → worker_id it is currently subscribed to (or None)
```

**On Worker disconnect (`worker_id=X`):**
1. Remove `worker_connections[X]`
2. Send `WORKER_STATUS(worker_id=X, status="disconnected")` to every WS in `worker_subscribers[X]`
3. Leave `worker_subscribers[X]` in place (Controllers remain subscribed; they will receive the new snapshot when Worker X reconnects)
4. Leave every other `worker_connections[Y≠X]` and `worker_subscribers[Y≠X]` completely untouched

**On Controller disconnect:**
1. Look up `controller_subscription[ctrl_ws]` → `worker_id=X`
2. Remove `ctrl_ws` from `worker_subscribers[X]`
3. Remove `ctrl_ws` from `controller_subscription`
4. Worker X's connection and every other Worker's connection/state is untouched

### 7.3 Controller Worker-Switching

When the user selects a different Worker (`worker_id=Y`) as active:

```
Does Controller hold a valid, non-stale state for worker_id=Y?
│
├─ YES → Display immediately; no network round-trip needed
│
└─ NO (no state, or state is flagged stale)
        │
        └─ Send RESYNC_REQUEST(worker_id=Y)
           Wait for FULL_SNAPSHOT(worker_id=Y)
           Apply; mark Y's state as valid
```

Switching to Worker Y must **never** discard or modify the tracked state for any other
`worker_id`. Each tracked Worker's state is an independent slot.

---

## 8. DOM Versioning (per Worker)

Each Worker maintains an independent monotonic integer version counter.
The Controller maintains a separate tracked version per `worker_id`.

```
Worker-01 version:  102      Worker-02 version:  45
Controller tracked: 102      Controller tracked:  45
                   (independent — no shared counter)
```

**Normal update flow (one Worker):**

```
Worker-01 DOM changes
Worker-01 version: 102 → 103
Worker-01 sends: DOM_UPDATE(worker_id="worker-01", base_version=102, version=103, ops=[...])

Controller checks: tracked_version["worker-01"] == 102?  YES
→ Apply ops; tracked_version["worker-01"] = 103
```

**Version mismatch (triggers resync for that Worker only):**

```
Controller tracked_version["worker-01"] = 100
Receives: DOM_UPDATE(worker_id="worker-01", base_version=102, version=103)

Controller: 100 ≠ 102 → REJECT update
Controller sends: RESYNC_REQUEST(worker_id="worker-01")
Worker-01 sends: FULL_SNAPSHOT(worker_id="worker-01", version=<new>)
Controller: reset tracked_version["worker-01"] = <new>
```

The mismatch and resync for `worker-01` has **zero effect** on `tracked_version["worker-02"]`
or any other Worker's tracked state.

**`timestamp` is never used for ordering.** It is metadata only. Clock skew between
Worker and Controller is assumed. `version` / `base_version` are the sole ordering authority.

---

## 9. Worker Authority Model

Each Worker is the **sole authoritative source** for its own browser's DOM state.
No other component — not the server, not the Controller, not another Worker — may
assert what a Worker's current DOM looks like.

Consequences:
- The server never stores or caches the Worker's HTML snapshot permanently.
- The Controller's held state for a `worker_id` is always a mirror; when uncertain, it
  discards its mirror and requests a fresh FULL_SNAPSHOT.
- After a Chrome crash, the Worker's version counter resets and a new FULL_SNAPSHOT is
  sent; the old version numbers are never reused.

---

## 10. Controller Synchronization Model

The Controller maintains, for each `worker_id` it is tracking:

| Per-worker_id state slot | Type | Description |
|---|---|---|
| `dom_tree` | parsed HTML structure | Current synchronized DOM |
| `current_version` | int | Last successfully applied version |
| `is_stale` | bool | True if state is known-invalid (reconnect, crash notification, etc.) |
| `url` | str | Current URL for this Worker |
| `status` | enum | `connected` / `disconnected` / `crashed` |

**Reconnect policy:** On Controller reconnect, **every** tracked `worker_id`'s `is_stale`
is set to `True` unconditionally. There is no branch that assumes old state might still be
valid. `RESYNC_REQUEST` is sent for each tracked Worker.

---

## 11. BeautifulSoup — HTML Normalization

**Used only for HTML parsing and normalization. Never for browser interaction.**

```
Selenium driver.page_source  →  Python Unicode str
                                     │
                                     ▼
                             BeautifulSoup(html, "html.parser")
                                     │
                                     ▼
                             website/parser.py normalization
                             - preserve legitimate Unicode
                             - handle malformed HTML
                             - handle HTML entities
                             - remove only genuinely problematic characters
                             - catch + recover from parse exceptions
                             - NEVER crash the Worker on malformed input
```

`driver.page_source` is a Python Unicode string. Do not encode/decode it unnecessarily
before passing to BeautifulSoup.

If parsing fails:
1. Catch the exception; log with `worker_id`.
2. Keep Chrome alive.
3. Retry when appropriate.
4. If still failing, send an ERROR (worker-scoped) and/or request RESYNC.
5. A single malformed character must never terminate the Worker process.

---

## 12. MutationObserver — Change Detection

JavaScript `MutationObserver` is injected into Chrome by the Worker to detect DOM changes.

```javascript
// Injected via Selenium execute_script
const observer = new MutationObserver(mutations => {
    // Batch signal — debounced (configurable via MUTATION_DEBOUNCE_MS env var, default: 100 ms)
    scheduleDomCapture();
});
observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    characterData: true
});
```

After the debounce fires, the Worker:

```
1. driver.page_source
2. BeautifulSoup + normalize
3. Compare with prior snapshot
4. Generate diff ops
5. Compress if appropriate
6. Send DOM_UPDATE(worker_id=...)
```

The MutationObserver produces **one batched signal**, not N per mutation. This prevents
flooding the WebSocket with micro-updates.

---

## 13. Zstandard Compression

Library: Python `zstandard`. Default level: `ZSTD_LEVEL=3` (from env).

| Payload type | Compress? |
|---|---|
| FULL_SNAPSHOT (large HTML) | Yes |
| DOM_UPDATE (large diffs) | Yes, if above threshold |
| CSS / resource blobs | Yes |
| COMMAND | No |
| COMMAND_RESULT | No |
| WORKER_STATUS / PING / PONG | No |
| Small diffs (< threshold) | No |

Compressed payloads use **binary WebSocket frames**. The `DOM_UPDATE` message includes
a `compressed: true` field so the receiver knows to decompress before parsing.

The compression decision and threshold are per-message. The threshold is configurable via `ZSTD_THRESHOLD` environment variable (default: `1024` bytes).
This rule applies identically regardless of how many Workers are connected.

---

## 14. CSS / Resource Caching

Each Worker's resource cache is independent of other Workers'.

```
Worker captures resource (CSS, image, etc.)
→ SHA-256 hash
→ Has Controller already acknowledged receiving this hash for this session?
  ├─ YES → skip; don't re-send
  └─ NO  → send resource once; Controller caches by hash
```

The cache is per-Worker session. A resource cached for `worker-01` does not
automatically count as received for `worker-02`.

---

## 15. Command Queue (per Worker)

Commands for a given Worker execute sequentially. Commands for different Workers
may execute concurrently.

```
worker-01 queue:  cmd-101 → cmd-102 → cmd-103
worker-02 queue:  cmd-201 → cmd-202

worker-01: 101 running ...         worker-02: 201 running ...
           102 waiting                         202 waiting
           103 waiting
```

A second command sent to `worker-01` does not start until `cmd-101` resolves
(`COMMAND_RESULT` received or timeout). The `worker-02` queue is entirely unaffected.

---

## 16. Error Recovery (per Worker, isolated)

### 16.1 WebSocket Disconnect (Worker side)

```
Worker WS drops
→ exponential backoff reconnect (self-contained)
→ re-authenticate
→ re-register with same worker_id
→ send FULL_SNAPSHOT (version reset — old version no longer valid)

Effect on other Workers: NONE
```

### 16.2 WebSocket Disconnect (Controller side)

```
Controller WS drops
→ reconnect
→ mark ALL tracked worker_id states as stale (is_stale = True)
→ for each tracked worker_id: send RESYNC_REQUEST
→ apply incoming FULL_SNAPSHOTs; clear stale flags
```

No branch where Controller assumes its old state is still valid after reconnect.

### 16.3 Chrome Crash (one Worker)

```
Worker detects WebDriverException / process exit
→ Restart Chrome (this Worker's process only)
→ Navigate to TARGET_DOMAIN
→ Reset version counter to 0
→ Send FULL_SNAPSHOT(worker_id=self, version=0, ...)
→ MutationObserver re-injected

Effect on other Workers: NONE
Server notified: via FULL_SNAPSHOT (implicit) + WORKER_STATUS if needed
```

### 16.4 Selenium Errors

All Selenium exceptions (`NoSuchElementException`, `TimeoutException`,
`StaleElementReferenceException`, `WebDriverException`, invalid selector,
navigation timeout) must be caught, logged (with `worker_id`), and returned as
`COMMAND_RESULT(success=False, error=<detail>)`. The Worker process must not crash.

### 16.5 Invalid / Malformed Messages

Any message failing validation (unknown `protocol_version`, missing required fields,
`worker_id` not in routing table, command not on allowlist) must produce an `ERROR`
response. It must never cause an unhandled exception in the server.

---

## 17. Security Model

| Concern | Approach |
|---|---|
| Authentication tokens | `WORKER_TOKEN`, `CONTROLLER_TOKEN` read from env vars only; never hard-coded |
| Secret logging | Tokens, passwords, cookies, and full patient HTML are never logged in production |
| Log redaction | Structured logging; sensitive fields (auth headers, cookie values) explicitly redacted |
| Command scope | Fixed allowlist enforced at server routing layer AND at Worker execution layer |
| Domain restriction | `TARGET_DOMAIN` from env; Worker rejects navigation outside that domain |
| Forbidden commands | `execute_shell`, `execute_python`, `execute_arbitrary_javascript` are never implemented |
| Persistent storage | No HTML snapshots stored permanently on Render |
| worker_id auth | Only a connection that has passed `AUTH` and `WORKER_REGISTER` can submit Worker-scoped messages |

---

## 18. Render Deployment

```python
# server/config.py
import os
PORT = int(os.environ.get("PORT", 8000))
```

The server starts with:

```
uvicorn server.main:app --host 0.0.0.0 --port $PORT
```

Required environment variables (`.env.example`):

```
WORKER_TOKEN=<strong-random-secret>
CONTROLLER_TOKEN=<strong-random-secret>
TARGET_DOMAIN=<the-authorized-website-domain>
ZSTD_LEVEL=3
PORT=8000           # Render overrides this automatically
```

**No polling endpoint is exposed.** The `/health` endpoint returns only `200 OK` with a
minimal body — it reveals no real-time state, DOM content, or Worker information.

---

## 19. Project File Structure

```
remote_website/
│
├── server/
│   ├── main.py              FastAPI app entry point; /ws/worker, /ws/controller, /health
│   ├── websocket_manager.py WS connection lifecycle, per-Worker and per-Controller
│   ├── worker_manager.py    Worker registration; worker_id → WS map; worker_id → subscribers map
│   ├── controller_manager.py Controller registration; Controller → subscribed worker_id map
│   ├── message_router.py    Route messages strictly by worker_id
│   ├── authentication.py    Token validation; rejects unauthenticated connections
│   ├── models.py            Server-side models
│   └── config.py            PORT, env var loading
│
├── worker/
│   ├── worker.py            Entry point; establishes worker_id at startup
│   ├── browser.py           Chrome/Selenium session management
│   ├── command_handler.py   Selenium command execution
│   ├── dom_tracker.py       DOM state, version, diff — independent per instance
│   ├── mutation_observer.py MutationObserver JS injection and debounce
│   ├── websocket_client.py  Outbound WS to server; tags all messages with this worker_id
│   ├── compression.py       Zstandard compress/decompress
│   └── config.py            WORKER_ID, SERVER_URL, WORKER_TOKEN, TARGET_DOMAIN, etc.
│
├── controller/
│   ├── controller.py        Entry point
│   ├── main_window.py       PySide6 main window; Worker list widget
│   ├── browser_view.py      Remote DOM renderer for selected Worker
│   ├── dom_renderer.py      Apply DOM ops to rendered view
│   ├── state_manager.py     Per-worker_id state slot (version, stale flag, dom_tree)
│   ├── websocket_client.py  WS connection to server
│   ├── worker_manager.py    Tracks all known Workers; manages active selection
│   ├── command_panel.py     Commands, always tagged with active worker_id
│   └── statistics_panel.py  Per-Worker bandwidth/performance stats
│
├── website/
│   ├── config.py            TARGET_DOMAIN and site-level config
│   ├── selectors.py         CSS/XPath selectors
│   ├── pages.py             Page-specific rules
│   ├── commands.py          High-level website commands
│   ├── parser.py            BeautifulSoup normalization; Unicode-robust
│   └── rules.py             Site-specific business logic
│
├── shared/
│   ├── protocol.py          Message type constants, protocol_version=1, WORKER_SCOPED_TYPES, allowlist
│   ├── messages.py          Construction/parsing/validation helpers; enforces worker_id
│   └── models.py            Pydantic models for all message types
│
├── tests/
│   ├── test_protocol.py     Protocol validation, worker_id enforcement, routing isolation
│   ├── test_compression.py  Zstandard round-trip, threshold logic
│   ├── test_dom_diff.py     All diff op types + no-op case
│   ├── test_sync.py         Version tracking, mismatch → resync, per-worker_id isolation
│   └── test_commands.py     Command queue ordering, failure handling, allowlist enforcement
│
├── requirements.txt
├── .env.example
├── ARCHITECTURE.md          ← this file
└── README.md
```

**Filenames are fixed.** Do not rename or create duplicates (`server2.py`, `protocol_v2.py`, etc.).
New files may only be added if there is a stated strong architectural reason, decided before creation.

---

## 20. Sequence Diagrams

The following diagrams use these actors:

```
CTRL  = Controller (PySide6)
SRV   = FastAPI Server (Render)
WK-A  = Worker A (worker_id = "worker-a")
WK-B  = Worker B (worker_id = "worker-b")
SITE  = Target Website
```

---

### 20.1 Initial Connect and Registration (one Worker + one Controller)

```
WK-A                    SRV                    CTRL
 │                       │                       │
 │── WSS /ws/worker ────►│                       │
 │◄─ 101 Switching ──────│                       │
 │                       │   WSS /ws/controller ─┤
 │                       │◄──────────────────────│
 │                       │── 101 Switching ──────►│
 │                       │                       │
 │── HELLO(role=worker) ►│                       │
 │── AUTH(token=...) ────►│                       │
 │                       │◄─ HELLO(role=ctrl) ───│
 │                       │◄─ AUTH(token=...) ────│
 │                       │                       │
 │◄─ AUTH OK ────────────│── AUTH OK ────────────►│
 │                       │                       │
 │── WORKER_REGISTER ────►│                       │
 │   (worker_id="wk-a") │                       │
 │◄─ ACK / WORKER_STATUS ─│                       │
 │                       │◄─ CONTROLLER_REGISTER ─│
 │                       │   (subscribe wk-a)    │
 │                       │                       │
 │── FULL_SNAPSHOT ──────►│── FULL_SNAPSHOT ──────►│
 │   (worker_id="wk-a") │   (worker_id="wk-a") │
 │   version=1           │   version=1           │
 │                       │                       │
 │ [DOM version: 1]      │                [tracked wk-a v=1]
```

---

### 20.2 Normal Update (click → DOM diff)

```
CTRL            SRV             WK-A            SITE
 │               │               │               │
 │─ COMMAND ────►│               │               │
 │  (wk-a, click,│               │               │
 │   #search)    │               │               │
 │               │─ COMMAND ────►│               │
 │               │  (wk-a only) │               │
 │               │               │─ click() ────►│
 │               │               │◄─ page changes─│
 │               │               │               │
 │               │               │ MutationObserver fires
 │               │               │ (debounced 50-200ms)
 │               │               │ page_source → BS4
 │               │               │ compare v1 → v2
 │               │               │ generate ops []
 │               │               │
 │               │◄─ DOM_UPDATE ──│
 │               │  (wk-a, base=1│
 │               │   ver=2, ops) │
 │               │               │
 │◄─ DOM_UPDATE ──│               │
 │  (wk-a, b=1, │               │
 │   v=2, ops)  │               │
 │               │               │
 │ check: tracked_v["wk-a"]=1 == base=1 → apply ops
 │ tracked_v["wk-a"] = 2        │               │
 │               │               │               │
 │◄─ COMMAND_RESULT ─────────────┤               │
 │  (wk-a, success=True)        │               │
```

---

### 20.3 DOM Version Mismatch → Resync

```
CTRL            SRV             WK-A
 │               │               │
 │ tracked_v["wk-a"] = 100      │
 │               │               │
 │◄─ DOM_UPDATE ──────────────────│
 │  (wk-a, base_version=102,    │
 │   version=103)               │
 │               │               │
 │ 100 ≠ 102 → REJECT           │
 │               │               │
 │─ RESYNC_REQUEST ─────────────►│
 │  (worker_id="wk-a")          │
 │               │               │
 │               │◄─ FULL_SNAPSHOT│
 │               │  (wk-a, v=103)│
 │               │               │
 │◄─ FULL_SNAPSHOT ──────────────│
 │  (wk-a, v=103)               │
 │               │               │
 │ tracked_v["wk-a"] = 103      │
 │ [re-render from scratch]     │
 │               │               │
 │ NOTE: tracked_v["wk-b"] is   │
 │       completely unaffected  │
```

---

### 20.4 Worker Reconnect (after transient disconnect)

```
WK-A                    SRV                    CTRL
 │                       │                       │
 │  [WS drops]           │                       │
 │                       │── WORKER_STATUS ──────►│
 │                       │  (wk-a, disconnected) │
 │                       │                       │
 │  [exponential backoff]│                       │
 │── WSS /ws/worker ────►│                       │
 │── HELLO ──────────────►│                       │
 │── AUTH ───────────────►│                       │
 │── WORKER_REGISTER ────►│                       │
 │   (worker_id="wk-a") │                       │
 │                       │── WORKER_STATUS ──────►│
 │                       │  (wk-a, connected)    │
 │                       │                       │
 │── FULL_SNAPSHOT ──────►│── FULL_SNAPSHOT ──────►│
 │  (wk-a, version=<new>)│  (wk-a, version=<new>)│
 │                       │                       │
 │                       │ [ctrl tracked_v["wk-a"] = new]
 │                       │                       │
 │ NOTE: WK-B's connection, state, and command queue
 │       are completely unaffected by WK-A's reconnect
```

---

### 20.5 Chrome Crash (one Worker, isolated recovery)

```
WK-A                    SRV                    CTRL
 │                       │                       │
 │  Chrome process exits │                       │
 │  WebDriverException   │                       │
 │  caught by worker.py  │                       │
 │                       │                       │
 │  → Restart Chrome     │                       │
 │  → Navigate TARGET_DOMAIN                     │
 │  → Reset version = 0  │                       │
 │                       │                       │
 │── WORKER_STATUS ──────►│── WORKER_STATUS ──────►│
 │  (wk-a, crashed)      │  (wk-a, crashed)      │
 │                       │                       │
 │── FULL_SNAPSHOT ──────►│── FULL_SNAPSHOT ──────►│
 │  (wk-a, version=0,   │  (wk-a, version=0)    │
 │   fresh HTML)         │                       │
 │                       │                       │
 │                       │ [ctrl tracked_v["wk-a"] reset to 0]
 │                       │                       │
 │  WK-B: zero effect. Its Chrome, connection,   │
 │  state, and queue are entirely undisturbed.   │
```

---

### 20.6 Controller Reconnect (all Workers treated as stale)

```
CTRL            SRV             WK-A            WK-B
 │               │               │               │
 │  [WS drops]   │               │               │
 │               │               │               │
 │  [reconnect]  │               │               │
 │── WSS ────────►│               │               │
 │── HELLO ──────►│               │               │
 │── AUTH ────────►│               │               │
 │── CONTROLLER_REGISTER ────────►│               │
 │               │               │               │
 │  [mark ALL tracked worker_ids stale]          │
 │               │               │               │
 │── RESYNC_REQUEST(wk-a) ───────►│               │
 │── RESYNC_REQUEST(wk-b) ────────────────────────►│
 │               │               │               │
 │◄─ FULL_SNAPSHOT(wk-a) ────────│               │
 │◄─ FULL_SNAPSHOT(wk-b) ─────────────────────────│
 │               │               │               │
 │  [tracked_v["wk-a"] = fresh]  │               │
 │  [tracked_v["wk-b"] = fresh]  │               │
 │               │               │               │
 │  NOTE: No "probably still valid" branch.      │
 │  On reconnect, every tracked state is stale.  │
```

---

### 20.7 Controller Worker Switching

```
CTRL            SRV             WK-A            WK-B
 │               │               │               │
 │ User clicks "worker-b" in Worker list         │
 │               │               │               │
 │ Does Controller hold valid state for wk-b?    │
 │   ┌─ YES (not stale) ─────────────────────────┤
 │   │  Display immediately; no network trip     │
 │   │                                           │
 │   └─ NO (no state, or stale) ─────────────────┤
 │         │               │               │     │
 │── CONTROLLER_REGISTER / subscribe(wk-b) ─────►│
 │── RESYNC_REQUEST(wk-b) ────────────────────────►│
 │                                               │
 │◄─ FULL_SNAPSHOT(wk-b) ─────────────────────────│
 │  [tracked_v["wk-b"] = received version]       │
 │  [render wk-b's DOM]                          │
 │               │               │               │
 │  NOTE: tracked_v["wk-a"], wk-a's DOM state,  │
 │  and wk-a's command queue are NOT touched.    │
 │  Switching Workers is a read-only selection   │
 │  operation from wk-a's perspective.           │
```

## 17. Multi-User Isolation & Session Security

The system supports multiple concurrent Controller sessions while preventing state leakage and enforcing per-session access controls.

### 17.1 Session Lifecycle

When a Controller authenticates successfully, the server creates a unique `ControllerSession`:
- Bound to a `session_id` (UUID4).
- Tracks a `last_activity_at` monotonic timestamp, updated on every incoming message.
- Tracks `authorized_worker_ids` derived from the provided token.

A background `session_reaper_loop` (configured via `SESSION_TIMEOUT_SECONDS`) periodically scans for idle sessions. If a session is idle for longer than the timeout, the server drops the connection with `ERROR(code="SESSION_TIMEOUT")` and logs the expiry.

### 17.2 Access Control Lists (ACL)

By default, any controller token valid against `CONTROLLER_TOKEN` has unrestricted access to all connected workers. 

To restrict access, deployers can define `CONTROLLER_ALLOWED_WORKERS`:
```
CONTROLLER_ALLOWED_WORKERS="tokenA:worker-1,worker-2;tokenB:worker-3"
```
When a token is provided in this mapping, the `SessionManager` restricts the session. Any attempt to subscribe to or interact with a worker outside this list yields an `UNAUTHORIZED` error and an `ACCESS_DENIED` audit log.

### 17.3 Structured Audit Logging

All security-relevant events are emitted as JSON lines to the `server.audit` logger.
Events include:
- `AUTH_SUCCESS` / `AUTH_FAILURE`
- `SESSION_START` / `SESSION_END`
- `ACCESS_DENIED`
- `SUBSCRIPTION_CHANGE`

Example:
```json
{"event": "ACCESS_DENIED", "timestamp": "2026-08-29T10:00:00Z", "session_id": "sess-abc...", "client_id": "gui-main", "worker_id": "w2", "reason": "Controller attempted to send COMMAND to unauthorized worker"}
```

---

## 18. Headless vs Headed Runtime Toggle & Proxy Management

Workers can seamlessly switch between Headless and Headed mode at runtime without a full process restart.

### 18.1 Runtime Configuration Switch

The `BrowserConfigMessage` (sent via the Controller's UI) instructs a Worker to cleanly restart its Chrome WebDriver session with a new `BrowserConfig` parameter set:
- **Headless mode**: Adds `--headless=new` if enabled.
- **Proxy**: Injects an isolated background extension via `--load-extension` for authenticated SOCKS5/HTTP proxies on the fly. The extension is built in memory and saves credentials temporarily without leaking into permanent config files.

### 18.2 State Resynchronization

Since switching modes completely recycles the browser (killing current page state), the Worker:
1. Re-initializes `webdriver.Chrome` with the new arguments.
2. Navigates back to `TARGET_DOMAIN`.
3. Resets `dom_version` to 0.
4. Spontaneously broadcasts a `FULL_SNAPSHOT` so Controllers can reconnect to the new visual state seamlessly.

## 19. Concurrency and Threading Architecture

To maintain absolute stability and prevent connection pool exhaustion in the single-connection `urllib3` architecture used by Selenium/WebDriver, the Worker enforces strict serialization of all browser interactions.

### 19.1 The Selenium Semaphore

All Selenium I/O operations (commands, mutations polling, DOM extraction, and crash recovery) are gated by a single `asyncio.Semaphore(1)` (`_selenium_lock`) per Worker.

- **Throughput is intentionally serialized per worker:** Incoming commands from the server are spawned as background tasks (`asyncio.create_task`) to immediately yield the ASGI/WebSocket receive loop (preventing TCP backpressure and `1011` keepalive ping timeouts). These tasks gracefully queue on `_selenium_lock` and execute synchronously against the WebDriver one at a time.
- **Horizontal scaling:** Scaling the system's throughput is achieved by adding more independent Worker instances to the pool (horizontal scaling), not by attempting to increase per-worker concurrency via multi-threading.

### 19.2 Lock Ordering (ABBA Prevention)

The architecture uses two primary locks:
1. `_selenium_lock` (Semaphore): Guards the thread-pool I/O bounds.
2. `_state_lock` (Lock): Guards the internal Python state (`dom_version`, `_last_snapshot_html`).

To mathematically prevent deadlocks, the architecture strictly mandates disjoint locking: the `_selenium_lock` is acquired, the I/O completes, the `_selenium_lock` is released, and only then is the `_state_lock` acquired to mutate internal variables. The two locks are never held simultaneously in a nested fashion.

---

## End of ARCHITECTURE.md
