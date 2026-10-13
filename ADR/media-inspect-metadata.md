# ADR: right-click "Inspect" — embedded ComfyUI metadata

## Context

ComfyUI embeds the generation graph in its output files, but the only way to read it
was to download the file and drop it into the ComfyUI editor. The right-click media menu
(`ADR/media-context-menu.md`) already had Save / Copy / Copy seed. Generated files are
written byte-for-byte from ComfyUI's `/view` (`_run_generation_core` only renames them),
so the metadata is still there to read.

## What was built

An **Inspect…** row on the menu, shown for both images and videos. It posts a chat
bubble holding a table of the file's metadata. Every row has a 📋 that copies its full
value.

### Server — `media_metadata.py` + `GET /api/image-metadata/<filename>`

- **Pure Python, no Pillow.** `read_raw_metadata` dispatches on the file extension:
  - **PNG**: walks the chunks and decodes `tEXt` / `zTXt` / `iTXt`.
  - **WEBP**: reads the `EXIF` RIFF chunk and parses the IFD0 ASCII tags in either byte
    order. `SaveAnimatedWEBP` writes `"Prompt:{…}"` / `"Workflow:{…}"`, which are split
    on the first `:`.
  - **MP4 / WEBM**: runs `ffprobe -show_format` and reads the container tags. Core
    `SaveVideo` writes `prompt` / `workflow` directly. VideoHelperSuite packs them into a
    JSON `comment` tag, sometimes with string-encoded values, so those are unpacked and
    decoded twice.
  - **JPG / GIF**: nothing is read from them.
- **Best-effort throughout.** A truncated file, a corrupt chunk or a missing ffprobe
  yields whatever was read so far, or `{}`; none of them raises. Scans are capped
  (`MAX_SCAN_BYTES`), and so is inflation (`MAX_TEXT_BYTES`, a zip-bomb guard).
- **`summarise_prompt_graph`** walks the API graph in numeric node order and picks out
  the **literal** inputs named in `SUMMARY_INPUTS`: prompts, sampling, models and size.
  Linked `[node, slot]` inputs are skipped. Rows carry the node's `_meta.title`, which is
  what tells the "Positive" and "Negative" `CLIPTextEncode` nodes apart. Seeds and other
  integers are sent as **strings**, as `/api/image-seed` does, because a 64-bit seed
  doesn't survive a JS `Number`.
- **The route** is validated exactly like `/api/image-seed` and returns 404 for a missing
  file. It also carries `file {name, size, mtime}` and the `seed_store` seed. That makes a
  JPG with nothing embedded still worth inspecting.

### Client

- **`renderMetadataTable`** (`utils.js`, pure, Jest-tested) returns `{html, copies}`. A
  📋 button holds only `data-copy-idx`, so a multi-megabyte workflow never ends up in a
  DOM attribute. The table shows these sections in order:
  1. File
  2. Prompts, Sampling, Models, Size
  3. Other metadata (e.g. A1111 `parameters`)
  4. Graphs: the API prompt graph and the UI workflow. Each sits in a collapsed
     `<details>` as pretty JSON, and its 📋 copies the **compact** JSON, which pastes
     straight into ComfyUI.
- **`copyText`** (`mediamenu.js`; `utils.js` stays DOM-free) uses `clipboard.writeText`
  only in a secure context. Otherwise it falls back to `execCommand('copy')` on a hidden
  textarea. The appliance is usually served over plain HTTP on a LAN, where
  `navigator.clipboard` doesn't exist, so without the fallback every 📋 would fail.
  **Copy image/video address** had the same latent bug and now uses `copyText` too.
- Inspecting from the lightbox **closes it**, because the table goes into the chat
  underneath.

## Not covered

- **Media outside `/images/` and `/archive-file/`** gets no menu. That covers
  `/references-file/` uploads and so on. Archive files were added later: they use
  `GET /api/archive-metadata/<path>`, and their seed row reads *No seed (archived)*.
- **JPG EXIF** (e.g. a custom node's `UserComment`) is not read.
- The summary is a flat allow-list of input names. A node pack using a different name
  for its prompt shows up only in the raw graph.
