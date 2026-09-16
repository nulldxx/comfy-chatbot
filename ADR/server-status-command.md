# `/server-status` — server health panel with ComfyTray start/stop

*Implemented. This records how it was built and why, not the plan it came from.*

## Context

The chatbot already knew which ComfyUI servers existed — `servers.json`, surfaced by
`/api/servers` and the `/server` picker — but nothing about whether any of them was
**up**. Picking a dead server just meant the next generation failed with a connection
error, and the only fix was to walk to the machine and use the tray menu.

`comfy-tray` (`~/Code/comfy-tray`, commit `1f0d98b`) grew a REST API exposing exactly
what its tray menu offers — `/api/status`, `/api/start`, `/api/stop` — on `0.0.0.0:8765`,
with no authentication, for a trusted home network. That closed the loop: the appliance
can ask each host whether ComfyUI is running and turn it on remotely.

The constraint that shaped the design: **not every server is managed by ComfyTray**. Some
are started by hand. So the panel reports two independent things per server and degrades
cleanly rather than treating a missing tray as a fault.

## What was built

### Two readings, deliberately independent

- **ComfyUI itself** — `GET /system_stats`, ComfyUI's own cheap liveness endpoint. Works
  on every server whether or not anything manages it, and is the honest answer to "can I
  generate on this right now?".
- **ComfyTray** — `GET /api/status` on the *same host* at `COMFY_TRAY_PORT`. Present ⇒
  the row gets ▶/■. Absent ⇒ *unmanaged*, reported as `reachable: false` and styled grey
  rather than red, because it is a normal state and not an error.

Keeping them separate is the whole point. A tray that is up while ComfyUI is down is the
case the Start button exists for; a tray that is absent while ComfyUI is up is a
perfectly healthy hand-started server. Collapsing them into one "status" would lose both.

### `server_status.py`

Imports only `config` and `requests` — never `app` — so it is unit-testable without a
Flask context, the rule `idle_lock.py` and `archive_browse.py` already follow.

- `probe_comfy` / `probe_tray` — every transport failure is **an answer, not a raise**.
  `_short_error` reduces `requests`' exception strings (which embed the URL and the
  urllib3 repr) to `connection refused` / `timed out`, which fit in a chat bubble.
- `_normalise_status` maps ComfyTray's camelCase to snake_case and **keeps its nulls**:
  `pid`/`port`/`uptimeSeconds` are null while stopped, and a coerced `0` would render as
  a real pid.
- `tray_power` raises `TrayError` carrying the tray's own message from its `500` body.
- `probe_all` fans out over a `ThreadPoolExecutor`. Serially, one dead host would cost
  every *other* server a full timeout each; the panel is meant to answer promptly.

### Routes

`GET /api/server-status` returns `{tray_port, servers: [...]}`; `POST /api/server-power`
takes `{server, action}`. Both `@login_required`, neither `@requires_output_storage` —
the panel must work in exactly the situations where things are broken.

`/api/server-power` **only posts to a host the catalogue names** (404 otherwise), and
addresses the tray by **host**, never by the `host:port` the client sent. This is
deliberately stricter than `/api/purge`, which forwards to any address given: that one
frees VRAM, this one *spawns a process* on the far end, so it must not be an open proxy.

`server_catalogue_with_default()` was lifted out of `api_servers` into `catalogue.py` so
the panel, the picker and the power guard all read one list and cannot disagree about
which servers exist.

### The asynchronous start, which drove the client design

ComfyTray's `Start` returns as soon as the process is spawned (`MainWindow.Snapshot`
reads `ComfyServerManager.ProcessId`/`StartedUtc`, both set immediately), so `running`
goes true seconds — for a cold start with models to load, most of a minute — before
ComfyUI is actually listening. A panel that trusted the tray's answer would show a green
"running" server that still refused every generation.

So `renderServerStatusGrid` (modelled on `renderJobsGrid`) marks the row **settling**
after a power action and re-polls every 5s until ComfyUI's real reachability agrees with
what the tray says it is doing, capped at 150s. Outside that it does not poll at all —
the same rule `/jobs` follows — with a ↻ button always present.

Idempotency is surfaced rather than hidden: `changed: false` renders as *Already
running.*, which is more use than silently redrawing the same card.

### Smaller decisions

- **One global `COMFY_TRAY_PORT`** (default 8765) rather than a per-server override. A
  per-entry `tray_port` would have meant touching `servers.json`, `/addserver` and
  `_restore_servers`' validator (which drops unknown keys, so it would silently lose the
  field across a settings restore) to buy a case that does not exist yet.
- **`fmtUptime`** is a new pure function in `utils.js`, not a reuse of `fmtDuration` —
  which is a number formatter for video lengths, not a clock. Pure, so it is unit-tested.
- **A single-column `.srv-list`**, not the `/jobs` grid: the rows are wide and mostly
  text, and read as a list of machines.
- The panel is a **superset of `/server`** — each row can make its server the active one.

## Verification

`python -m pytest tests/` (808 pass), `npm run test:js` (203 pass), `node --check` over
`static/js/`. `tests/test_server_status.py` pins the ComfyTray wire contract with
`requests` patched — camelCase mapping, nulls while stopped, idempotent `changed: false`,
a `500` becoming a `TrayError`, an absent tray as `reachable: false`, and that `probe_all`
really overlaps (a `threading.Barrier` that breaks if the probes run serially).
`tests/test_app_routes.py::TestServerStatusEndpoints` covers the routing and the guard,
including that the right host on the wrong port is still a 404.

End to end: run `/server-status` with ComfyUI stopped on a tray-managed host, press
Start, and watch the ComfyUI line flip from *not responding* to *running* a few seconds
after the tray icon goes green — that gap is the asynchronous start, and is the thing
this panel is built around.

## Limits

- **No restart button** — stop-then-start would have to straddle the asynchronous start.
- **ComfyTray has no authentication**, by its own design, so this is only safe on a
  trusted network. Reaching it from another machine also needs an inbound firewall rule
  the per-user MSI cannot add (its About box prints the `netsh` command); a tray that is
  up but firewalled is indistinguishable from no tray, and reads *unmanaged*.
- The panel reports; it does not act on its own. Nothing auto-starts a server before a
  generation.
