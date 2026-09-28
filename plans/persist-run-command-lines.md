# Persist the command line that starts a server-side run

## Problem

`/sequence`, `/video-sequence` and the `/api/batch-run` commands (`/multi-prompt`,
`/iterations`, `/i2v <N>`, …) draw their command line in the chat and then hand the
session file to the server (`state.liveRunSession`), which suppresses the client's
auto-save for the whole run. The server only appends per-shot prompt/image pairs, and
nothing re-saves when the run ends — so the command line (and anything else unsaved,
e.g. an image whose 1.5s debounced save was still pending) never reaches disk. A
reload or `/session-load` shows the shots without the command that produced them.

## Fix

Flush the client's recording save **before** posting the run:

- `doRecordSave()` returns its fetch promise (resolving even on failure).
- New `flushRecordSave()` cancels the pending debounce and saves immediately.
- `runSequenceRunJob` and `runBatchJob` `await` it before `POST /api/sequence-run` /
  `/api/batch-run`. The save completes before the server becomes the sole writer, so
  there is no race with its appends.

A failed save never blocks the run. No server change.
