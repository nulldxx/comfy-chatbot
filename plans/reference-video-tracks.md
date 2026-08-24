# Reference video tracks: per-video video/audio checkboxes replace the paired-audio slots

## Context

`/references` offers four slot groups — 9 images, 3 videos, 3 **video-paired audios**,
3 standalone audios — capped at 12 files total.

The video-paired audio rows are a design mistake. They are separate user-settable upload
slots matched to a video **by array index only**; nothing in the app links `Video 2` to
`Video audio 2` (`generation_service.py:398-407` fills the two placeholders
independently, `_resolve_references` at `app.py:1219-1259` resolves them from unrelated
payload arrays). The natural user error is attaching a clip and forgetting its audio, or
pairing mismatched files. The audio row also *rejects* video files (`commands.js:200`,
`commands.js:225`), so "use this clip's audio" means demuxing by hand elsewhere.

That audio isn't an independent asset — it's a channel of the clip already in the video
slot. ComfyUI's VHS `Load Video (Upload)` node exposes an **AUDIO output alongside
IMAGE**, so one uploaded file feeds both inputs of a MiniMax H3 R2V graph, with no
demuxing on our side.

**Outcome:** the three paired-audio rows disappear. Each video row gains two checkboxes,
**video** and **audio**, choosing which tracks of that one clip reach the workflow. The
standalone audio group is untouched.

## Decisions

- **Template convention: one loader node, both outputs.** A single VHS node holds
  `<REFERENCE_VIDEO_n>`; its IMAGE and AUDIO outputs both feed the consumer. Unticking a
  box drops just that link, keeping the node.
- **`<REFERENCE_VIDEO_AUDIO_n>` stays supported.** If a template carries the token (the
  two-node convention), it's filled with the *same* clip when audio is ticked and
  sentinel-stripped when it isn't. Free backward compatibility, and an escape hatch.
- **A both-tracks video costs 2** of the 12; one track = 1; both off = inactive, 0.
- **Both boxes default to ticked** on drop.
- **Fail loudly.** If we can't determine which output is which, error the job rather than
  render the opposite of what was ticked.

## Implementation

Order matters — steps 1-4 are backend and independently testable, 5-7 are frontend.

### 1. `workflow.py` — partial-link surgery (pure, no server)

Two helpers after `strip_reference_nodes` (`workflow.py:112-143`), which stays as-is for
whole-node removal:

- `drop_node_output_links(workflow, node_id, output_indices)` — delete every consumer
  input wired to `(node_id, idx)` for those indices; the **producer survives**. Same
  semantics as the existing strip: an absent optional input, not a rewire. Returns the
  `(consumer_id, input_key)` pairs removed.
- `node_link_output_indices(workflow, node_id, name_contains="audio")` — fallback
  classifier splitting the indices `node_id` actually drives by consumer *input name*.
  Returns `(matching, other)`.

Also add `reference_marker(token)` beside `reference_sentinel` (`workflow.py:11-19`),
returning e.g. `__REF_NODE_REFERENCE_VIDEO_1__`. Active single-node video tokens are
substituted with the marker, the node is located by it immediately after `json.loads`,
and the real uploaded filename is written into that input. This removes the one silent
failure of a filename scan: the same clip in two slots with different flags is
indistinguishable by filename, and picking wrong mis-wires the graph invisibly.

All stripping runs **before** `convert_ui_to_api_format`, so this — like the existing
strip and `strip_lora_nodes` — only works on **API-format** templates. Pre-existing, but
worth stating in the docs.

### 2. `ComfyServer.py` — `get_node_output_types(class_type, timeout=10)`

`GET http://{server}/object_info/{class_type}` → its `output` list, e.g.
`["IMAGE","MASK","AUDIO","VHS_VIDEOINFO"]`, so we drop exactly the AUDIO-typed links
(audio unticked) or every non-AUDIO link (video unticked). Better than hardcoded indices:
VHS's output tuple has shifted across releases, but the type *names* are stable.

- Module-level cache keyed `(self.server, class_type)` under a `threading.Lock` —
  `ComfyServer` is constructed per job, so an instance cache would re-fetch every time,
  and two hosts can have different custom-node builds. Only cache non-empty results.
- 10s timeout, matching `poll_status` (`ComfyServer.py:219`). Normalise non-str entries
  (combo outputs) to `"COMBO"`. `quote()` the class name.
- Called only when a drop is actually needed — never on the both-tracks hot path.

### 3. `generation_service.py` — fill rules

Replace the two video loops at `:402-405` with one loop per slot:

- Template has `<REFERENCE_VIDEO_AUDIO_n>`, **or** the slot is inactive → today's
  behaviour: fill each token independently, sentinel + strip when absent.
- Otherwise (single-node) → fill `<REFERENCE_VIDEO_n>` whenever **either** track is
  wanted (the node holds the file even in the audio-only case), and if exactly one track
  is wanted, record a pending drop.

After `strip_reference_nodes` (`:453`) — deliberately after, so inactive slots' nodes are
already gone — resolve each pending drop: marker → node id → `class_type` →
`get_node_output_types` → indices → `drop_node_output_links`. On a transport failure fall
back to `node_link_output_indices`; if that's also inconclusive, raise a `ValueError`
naming the slot, the class, and the two-node escape hatch.

No signature change at `:251`: the flags fall out of `input_reference_videos[i] is not
None` / `input_reference_video_audios[i] is not None`. When both tracks are on, the two
entries are the **same `Path` object**, so `ref_upload_cache` (`:374-380`, keyed on
`str(path)`) uploads to ComfyUI once.

### 4. `app.py` — `_resolve_references` (`:1219-1259`)

Wire format becomes `{ images: [9], videos: [3], videoTracks: [3 × {video,audio}], audios: [3] }`.

- Videos leave `slot_specs`; resolve each active video **once** with `VIDEO_EXTS` and
  write the same `Path` into both `input_reference_videos[i]` and
  `input_reference_video_audios[i]` per its flags. The audio kwarg no longer has an
  extension class of its own — it *is* the video file.
- Cap: images + audios count 1 each; each video counts `use_video + use_audio` (0-2).
- A missing `videoTracks` key (stale browser tab across a deploy) means video-track-only —
  graceful, so no legacy `videoAudios` branch is needed server-side.
- Hoist the hardcoded `12` (`:1247`, `:1249`) to `REFERENCE_MAX_FILES` in `config.py` so
  Python and JS quote one number. Rewrite the docstring. Both routes (`:1297`, `:1365`)
  are unchanged — they just `**ref_kwargs`.
- All four kwargs keep their full `[None]*n` shape.

### 5. `static/js/state.js` — shape, migration, counting

Keep `videos` as a URL array and add a **parallel** `videoTracks` array. Do not turn
video slots into objects: `padSlots` (`:29`) fills with `null` and `countReferenceFiles`
(`:64`) uses `filter(Boolean)`, and `refSlotGet`/`refSlotSet`/`buildReferenceRow` all
consume a URL — objects fork every one of them for no gain.

- `REFERENCE_SLOT_COUNTS` (`:25`) drops `videoAudios`; it stays the source of truth for
  "array of URL-or-null" groups, so `videoTracks` is deliberately not a member. Add
  `REFERENCE_TRACK_DEFAULT = { video: true, audio: true }`.
- New `padTracks(arr, n, legacyAudios, videos)` beside `padSlots`.
- `cloneReferences` (`:50`) migration: a legacy `videoAudios[i]` maps to `audio: true`
  **only when `videos[i]` is also set** — an orphaned legacy audio otherwise silently
  arms the audio track on a slot the user later fills with an unrelated clip. Accepted
  loss: a legacy pairing of *different* files can't survive the one-clip model.
- `countReferenceFiles` charges a video per ticked track, 0 when the slot is empty.

Because `cloneReferences`/`newReferences` are the single funnel, `saveSession`
(`chat.js:1273`), `restoreSession` (`chat.js:1312`, `:1338-1340`), `/settings-save`
(`commands.js:2775`), `/settings-restore` (`:2819`) and `newChat` (`:876`) need **no
edits**.

### 6. `static/js/chat.js` — `referencesForRun` (`:788-800`)

Emit `videoTracks` alongside `videos`, force a flag false when the slot is empty, and
send `videos[i] = null` when both tracks are off so the backend never sees an inactive
clip. Drop `videoAudios` from the payload and from the `any` test.

### 7. `static/js/commands.js` — the table

- `buildReferenceSlots` (`:47-76`): delete the `push('videoAudios', …)` block (`:67-70`);
  give the video group a `{ tracks: true }` marker via a new optional `extra` argument to
  `push`, and update its header/notes.
- Add `refTracks(slot)` beside `refSlotGet`/`refSlotSet` (`:80-90`), and `refTrackSet`
  which refuses a tick that would breach the cap.
- `refSlotWouldExceed` (`:93`) becomes cost-aware (a fresh video costs 2). With exactly
  one slot free, accept a dropped video as video-only and flash an amber *"only 1 slot
  left — audio track off"* — otherwise the last slot looks broken for video.
- `buildReferenceRow` (`:136-238`): `render()` has early returns (`:167`, `:175`), so
  rename its body `renderZone()` and make `render() { renderZone(); syncTracks(); }`,
  where `syncTracks` is a no-op unless `slot.tracks`. Insert the checkbox column between
  the dropzone and ✕ (`:240-243`) — outside `zone`, so clicks don't open the file picker.
  Each box reverts itself and flashes on refusal. A live caption reads `2 files` /
  `1 file` / `inactive` (amber, zone dimmed at 0.5 opacity).
- The ✕ handler (`:229`) must reset tracks to the default, else a stale `audio:false`
  makes the next drop cost an unpredictable amount.
- Extend the hint (`:126`) with the per-track cost rule; update `/help` (`:2064`), which
  still advertises "3 video-paired audios".

### 8. Docs

- `CLAUDE.md:296` — `<REFERENCE_VIDEO_AUDIO_n>` is now *the audio track of reference
  video n*, never a user-uploaded file.
- `CLAUDE.md:331-370` — new slot summary, the `videoTracks` key, the 2-files rule, the
  migration rule, the `/object_info` + `drop_node_output_links` mechanism, the failure
  policy, and the API-format-only constraint on stripping.
- New `ADR/reference-video-tracks.md` (the existing `ADR/references-table.md:20-27` is
  already stale — it documents the pre-expansion single-slot scheme): why tracks not
  slots, why a parallel array, why one node over two, why `/object_info` over hardcoded
  indices, the cap rule, the accepted legacy loss.
- Per repo convention, copy this plan to `plans/reference-video-tracks.md` and commit it
  before implementation.

## Verification

```bash
cd /home/ben/Code/comfy-chatbot
python -m pytest tests/ -q                  # full Python suite
npm run test:js                             # jest (ESM)
for f in static/js/*.js; do node --check "$f" || echo "FAIL $f"; done
npx pyright
./scripts/test-all                          # Docker container tests (slow)
```

`./scripts/test-all` runs only `test_imports.py` and `test_simple.py` for Python, so run
the full pytest separately.

**Tests to add/update**

- `tests/test_workflow.py` — `drop_node_output_links` (drops only named indices, producer
  survives, other producers untouched, no-op on empty), `node_link_output_indices`,
  `reference_marker` (doesn't cross-match `reference_sentinel`).
- `tests/test_comfy_server.py` (new) — patch `ComfyServer.requests.get`: parses `output`,
  unknown class → `[]`, combo entries normalise, second call for the same
  `(server, class_type)` doesn't re-fetch, a different server does, empty isn't cached.
- `tests/test_generation_service.py` — keep `test_minimax_slots_fill_supplied_and_strip_missing`
  (`:528-586`) as the two-node case, with the audio slot now the *same* `Path` and an
  assertion that `upload_media` is called once for it. Add: audio-unticked drops the AUDIO
  link and keeps the node; video-unticked drops the IMAGE link; both ticked never calls
  `get_node_output_types`; both off strips the node; `/object_info` failure falls back to
  input names; both inconclusive raises naming the slot.
- `tests/test_app_routes.py` — swap `"videoAudios": []` for `"videoTracks": []` at `:900`;
  `:846-852` needs no change. Add: both tracks share one resolved path (fails today —
  `AUDIO_EXTS` would 400 an `.mp4`); audio-only leaves the video kwarg `None`; an inactive
  slot is never resolved (a nonexistent URL doesn't 404); 9 images + 2 both-track clips =
  13 → 400.
- `tests/js/state.test.js` (new — jest is `testEnvironment: node`, and `state.js` only
  imports `utils.js`, whose sole `window` use is inside a function body, so it loads
  clean): `countReferenceFiles` arithmetic including flags on an empty slot, and every
  `cloneReferences` migration branch, especially legacy audio with **no** video → `audio:
  false`.

**Manual end-to-end**

1. `/references` shows three groups; no "Video-paired audio".
2. Drop a clip on Video 1 → both boxes tick, caption `2 files`, counter `0 → 2`.
3. Untick audio → `1 file`; untick video too → `inactive`, dimmed, counter `0`, clip kept.
4. Fill to 11 files, drop a video → lands audio-off with the amber note. At 12, ticking a
   second track flashes red and reverts.
5. ✕ then re-drop → both boxes on again.
6. Save chat → `/newchat` → reload from the sidebar: clip and flags restored. Same through
   `/settings-save` → change → `/settings-restore`.
7. Load a **pre-change** chat with `videos[0]` + `videoAudios[0]` → both boxes ticked; one
   with `videoAudios[1]` but no `videos[1]` → slot 2 empty, audio unticked.
8. Run the MiniMax template: both tracks → one "Uploading reference video 1" in the
   progress log, not two; audio-only → the IMAGE link is dropped and the clip contributes
   no visuals; both off → the node is stripped.
9. Block `/object_info` (or stop ComfyUI mid-job) and confirm the fallback, then the hard
   error, read legibly in the chat.

**Out of repo scope, needed for the feature to do anything:** author the MiniMax graph in
`~/comfy-workflows/` with one VHS `Load Video (Upload)` per `<REFERENCE_VIDEO_n>`, IMAGE
and AUDIO both wired to the MiniMax node, **exported in API format**.
