# Per-server auto-purge flag

## Context
Auto-purge (`generation_service.py` `purge_generation_finished` → `_auto_purge`) frees GPU
memory on every ComfyUI server `AUTO_PURGE_SECONDS` after its last generation. It is global,
so there is no way to keep models warm on one box while purging another. Make it a
per-server flag in `servers.json`, default **on**, toggled by a checkbox on each
`/server-status` card (user chose this panel; no `/servers` command exists).

## Data model
- `servers.json` entry gains optional `"auto_purge": bool`. **Absent = on**, so existing
  files and the synthesised COMFY_SERVER default behave as today.
- Address not in the catalogue (e.g. `/api/purge`-style ad-hoc address) = on.

## Backend
1. **`catalogue.py`**
   - `server_auto_purge_enabled(address) -> bool`: find entry in
     `server_catalogue_with_default()` where `f"{host}:{port}" == address`; return
     `entry.get("auto_purge", True) is not False`; not found → `True`. Swallow errors → `True`.
   - `set_server_auto_purge(address, enabled) -> dict | None`: load
     `server_catalogue_with_default()`, set flag on matching entry, write
     `{"servers": [...]}` to `COMFY_WORKFLOW_DIR/servers.json`; `None` if not found.
     (When servers.json was absent this materialises the default entry — equivalent list.)
2. **`generation_service.py`**
   - `purge_generation_finished`: when `active == 0`, cancel timer as now, but only start a
     new one if `server_auto_purge_enabled(server_address)`. Read happens outside
     `purge_lock` (compute flag first) to keep file IO out of the lock.
   - `_auto_purge`: re-check the flag before `free_memory()` (covers toggle-off racing a
     pending timer); log skip.
3. **`server_status.probe_one`**: add `"auto_purge": entry.get("auto_purge", True) is not False`.
4. **`app.py`**
   - New `POST /api/server-auto-purge` (`@login_required`, no `@requires_output_storage`):
     body `{server, enabled}`; `enabled` must be bool → 400; unknown server → 404 (same
     catalogue-only rule as `/api/server-power`); OSError → 500. On disable call
     `cancel_auto_purge(address)`. Returns `{server, auto_purge}`.
   - `api_add_server`: preserve an existing same-name entry's `auto_purge` when replacing it.
   - `_restore_servers`: keep `auto_purge` when it is a bool (currently dropped as unknown key).

## Frontend (`static/js/commands.js`, `renderServerStatusGrid` → `buildCard`)
- Append to `.srv-card-actions` a `<label>` with checkbox "Auto-purge GPU memory",
  `checked = server.auto_purge !== false`, title explaining idle `AUTO_PURGE_SECONDS` purge.
- On change: disable box, POST `/api/server-auto-purge` via `parseJsonResponse`; on
  `data.error`/reject revert `checked` and set a `notes` entry with the error; re-enable.
  No full `load()` needed on success (avoids re-probing).
- Small CSS in `static/css/chat.css` for label alignment inside `.srv-card-actions`.
- Update `/help` notes for `/server-status` if it lists card features.
- Run `node --check static/js/commands.js` (curly-quote pitfall).

## Tests
- `tests/test_catalogue.py`: enabled default true, explicit false, unknown address true;
  `set_server_auto_purge` writes flag / returns None for unknown.
- `tests/test_generation_service.py`: `purge_generation_finished` schedules no timer when
  disabled, schedules when enabled (patch `server_auto_purge_enabled`, `threading.Timer`).
- `tests/test_server_status.py`: `probe_one` passes `auto_purge` through (default true).
- New route tests (existing app test pattern with forged session): 400 non-bool, 404
  unknown, 200 writes file + calls `cancel_auto_purge` on disable; `_restore_servers`
  keeps bool flag; add-server preserves flag.

## Docs / process
- Commit this plan as `plans/per-server-auto-purge.md` before implementing; afterwards
  rewrite as `ADR/per-server-auto-purge.md`.
- CLAUDE.md: add a bullet to the "Server status" section describing the flag, default,
  and route.

## Verification
- `python -m pytest tests/test_catalogue.py tests/test_generation_service.py tests/test_server_status.py -v`, then `./scripts/test-all` and `npm run test:js`.
- Manual: `docker-compose up --build -d`, open `/server-status`, untick a server, confirm
  `servers.json` gains `"auto_purge": false`, run a generation, confirm no
  "Auto-purged GPU memory" log line after `AUTO_PURGE_SECONDS` (set low, e.g. 20); re-tick
  and confirm purge logs.
