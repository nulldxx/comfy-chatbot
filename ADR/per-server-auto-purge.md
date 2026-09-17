# Per-server auto-purge flag

## Context
Auto-purge frees GPU memory on a ComfyUI server `AUTO_PURGE_SECONDS` after its last
generation finishes. It applied to every server, so a box you wanted to keep warm
reloaded its models after every idle gap.

## Decision
Auto-purge is a per-server flag, stored as `auto_purge` on each `servers.json` entry and
defaulting to **on**.

- **Storage.** An absent key means on, so existing catalogues and the synthesised
  `COMFY_SERVER` default behave exactly as before. An address that isn't in the catalogue
  also counts as on: the flag only opts a known server out.
- **Lookup.** `catalogue.server_auto_purge_enabled(address)` matches `host:port` against
  `server_catalogue_with_default()`, and any read failure falls back to on.
- **Timers.** `generation_service.purge_generation_finished` still cancels any pending
  timer, but schedules a new one only when the flag is on. The file read happens outside
  `purge_lock`. `_auto_purge` checks the flag again when it fires, in case the box was
  unticked while the timer was pending.
- **API.** `POST /api/server-auto-purge {server, enabled}` is `@login_required`. It
  accepts only catalogue servers (404 otherwise, the same rule as `/api/server-power`),
  returns 400 when `enabled` is not a bool, and cancels any pending purge when the flag is
  switched off. `catalogue.set_server_auto_purge` writes the list. When `servers.json` did
  not exist yet, this writes the default entry out as a real file.
- **Preserved elsewhere.** `/api/add-server` keeps the flag when a server is re-added
  under the same name. `_restore_servers` keeps it when it is a bool.
- **UI.** Each `/server-status` card gets an *Auto-purge GPU memory* checkbox, fed from
  the `auto_purge` value that `server_status.probe_one` passes through. A failed save
  puts the box back and shows the error inline.

## Not covered
- Manual `/purge` ignores the flag; it is an explicit request.
- The purge delay is still one global `AUTO_PURGE_SECONDS`.
