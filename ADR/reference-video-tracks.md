# Reference video tracks: per-clip video/audio checkboxes

**Status:** implemented. Supersedes the "video-paired audio" slots described in
[`references-table.md`](references-table.md).

## Problem

The `/references` table shipped with four independent slot groups — 9 images, 3 videos,
3 **video-paired audios**, 3 standalone audios — capped at 12 files.

The paired-audio rows were a mistake:

- They were matched to a video **by array index only**. Nothing linked `Video 2` to
  `Video audio 2`: the two placeholders were filled from unrelated payload arrays, and no
  code checked that both were set or that they belonged together.
- The audio row **rejected video files**, so "use this clip's audio" meant demuxing by
  hand in another tool first.
- Conceptually wrong: that audio is not an independent asset, it's a channel of the clip
  already sitting in the video slot.

## Decision

Each video row carries **two checkboxes, video and audio**, selecting which tracks of its
one clip reach the workflow. Both default to ticked. The paired-audio group is gone; the
standalone audio group is untouched.

ComfyUI does the demuxing: VHS `Load Video (Upload)` emits **AUDIO alongside IMAGE**, so
one uploaded file feeds both inputs. No ffmpeg on our side, no second upload, no extra
stored file.

### Why these choices

- **Tracks, not slots.** The failure mode being removed is a silent mismatch between two
  things the user had to keep in sync manually. One clip, two toggles, can't desync.
- **A parallel `videoTracks` array, not objects in `videos`.** `padSlots`/`countReferenceFiles`
  filter on truthiness and `refSlotGet`/`refSlotSet`/`buildReferenceRow` all consume a URL;
  an always-truthy object slot would fork every one of them. The parallel array confines
  the new concept to three places and leaves session save/restore and the `/settings-save`
  stack untouched, since they funnel through `cloneReferences`.
- **One loader node, not two.** A single VHS node driving both consumer inputs is the
  tidier graph and decodes the clip once. The cost is that turning a track off is
  link surgery rather than node removal — `drop_node_output_links()`, which keeps the
  producer because it still has to load the clip for the other track. The two-node
  convention (`<REFERENCE_VIDEO_AUDIO_n>` on a second loader) still works and is the
  documented escape hatch.
- **`/object_info` over hardcoded output indices.** VideoHelperSuite's output tuple has
  changed across releases (`IMAGE, INT, AUDIO, …` vs `IMAGE, MASK, AUDIO, …`), but the
  type *names* are stable, so `ComfyServer.get_node_output_types()` asks the server which
  index carries AUDIO. Cached per (server, class_type) — a `ComfyServer` is built per job,
  and two hosts can run different custom-node builds. Never called on the both-tracks hot
  path.
- **Fail loudly.** If neither the declared types nor the consumer input names can identify
  the track, the job errors. Keeping the link would render the opposite of what was ticked
  — invisible until the user has paid for a render — and would desync the 12-file budget,
  resurfacing later as a confusing *remote* MiniMax error.
- **A per-token marker to find the loader.** `reference_marker()` is substituted for the
  filename and overwritten immediately after `json.loads`. Scanning for the uploaded
  filename instead would be ambiguous when the same clip sits in two slots with different
  flags, and picking wrong mis-wires the graph silently.
- **A both-tracks clip costs 2 of the 12.** MiniMax counts each track as a file. A drop
  with exactly one slot free lands video-only with an amber note rather than being
  refused, so the last slot doesn't look broken.

## Implementation

**Backend**

- `workflow.py` — `drop_node_output_links()` (delete consumer inputs wired to given
  outputs, producer survives), `node_link_output_indices()` (fallback classifier by
  consumer input name), `reference_marker()` / `find_marked_node()`.
- `ComfyServer.py` — `get_node_output_types(class_type)` over `GET /object_info/<class>`,
  module-level cache under a lock, 10s timeout, combo outputs normalised.
- `generation_service.py` — one loop per video slot choosing the two-node or single-node
  path; `_apply_track_drop()` resolves each pending drop after `strip_reference_nodes`.
- `app.py` `_resolve_references` — videos leave `slot_specs`; each active clip resolves
  once with `VIDEO_EXTS` and the same `Path` goes into both kwargs per its flags. Cap
  charges per track. A missing `videoTracks` key (stale browser tab) reads as video-only.
- `config.py` — `REFERENCE_MAX_FILES`, so Python and JS quote one number.

**Frontend**

- `state.js` — `videoTracks` array, `REFERENCE_TRACK_DEFAULT`, `padTracks()` (empty slot →
  default; legacy clip → audio from the old `videoAudios` entry), cost-aware
  `countReferenceFiles`.
- `commands.js` — the video group's `tracks: true` marker, `refTracks`/`refTrackSet`,
  cost-aware `refSlotWouldExceed` with the one-slot-left degrade, the checkbox column and
  its caption, tracks reset on ✕.
- `chat.js` — `referencesForRun` emits `videoTracks` and nulls an inactive clip.

**Tests** — `test_workflow.py` (the new helpers), `test_comfy_server.py` (new file,
`/object_info` parsing and caching), `test_generation_service.py` (both conventions, the
fallback and the hard failure), `test_app_routes.py` (shared path, single track, inactive
slot, legacy payload, per-track cap), `tests/js/state.test.js` (new file, the counting
arithmetic and every migration branch).

## Consequences

- The workflow JSON must be authored to match: one VHS `Load Video (Upload)` per
  `<REFERENCE_VIDEO_n>`, IMAGE and AUDIO both wired to the MiniMax node, exported in
  **API format** (all node stripping runs before the UI→API conversion).
- A legacy session that paired a video with a *different* audio file loses that audio on
  restore. There's no way to express it under the one-clip model.
- Reference audio for a video can no longer be substituted — by design. Standalone audio
  slots remain for audio that isn't a clip's own track.
