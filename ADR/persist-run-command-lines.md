# Persist the command line that starts a server-side run

## Context

`/sequence`, `/video-sequence` and every `/api/batch-run` command (`/multi-prompt`,
`/iterations`, `/i2v <N>`, `/face-detail <N>`, …) draw their command line in the chat,
then set `state.liveRunSession`, which makes the server the sole writer of the session
file and suppresses the client's auto-save (`scheduleRecordSave` / `doRecordSave`) for
the whole run. The server appends only per-shot prompt/image pairs
(`append_session_image` / `append_session_note`), and nothing re-saves when the run
ends. So the command line never reached disk unless a later client-side generation
happened to trigger a full save — reloading or `/session-load` showed the shots without
the `/sequence …` that produced them. The same gap swallowed an image whose 1.5s
debounced save was still pending when the run started.

## Decision

The client flushes its recording save **before** posting the run:

- `doRecordSave()` returns its fetch promise (always resolving — its chain ends in
  `.catch(() => {})`), and `flushRecordSave()` cancels the pending debounce and calls it.
- `runSequenceRunJob` and `runBatchJob` (`chat.js`) chain the run's `POST` onto
  `flushRecordSave()`.

Because the save completes before the run is even requested, it cannot race the
server's appends, which then read-modify-write a file that already holds the command
line. A failed save does not block the run.

## Alternatives rejected

Sending the command line to the server and appending it there when the run starts was
the first proposal. It fixes only the command line, not the other unsaved state (a
pending image, other slash-command lines since the last save), and adds a request field
to two endpoints for no extra robustness — the flush happens while the tab is
necessarily still open.

## Consequences

- Starting a run costs one extra `POST /api/chats` round trip before the job starts.
- `restoreSession` needed no change: the command is an ordinary user message.
