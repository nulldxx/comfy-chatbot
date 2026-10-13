"""Read the generation metadata ComfyUI embeds in its output files.

Backs the right-click "Inspect" menu item. Output files are written byte-for-byte
from ComfyUI's /view (see _run_generation_core), so whatever ComfyUI embedded is
still there:

- PNG  — tEXt / zTXt / iTXt chunks: ``prompt`` (API graph), ``workflow`` (UI graph),
         plus anything else a node pack wrote (e.g. A1111-style ``parameters``).
- WEBP — an EXIF chunk whose IFD0 ASCII tags hold ``"Prompt:{…}"`` (0x0110) and
         ``"Workflow:{…}"`` (0x010F), as SaveAnimatedWEBP writes them.
- MP4 / WEBM — container format tags: ``prompt`` / ``workflow`` (core SaveVideo), or
         a ``comment`` tag holding a JSON object of both (VideoHelperSuite).

Pure Python — there is no Pillow in requirements.txt — apart from ffprobe for video,
which the image already ships for /video-splice. Every reader is best-effort: a
truncated or odd file yields ``{}``, never an exception. Never imports ``app``.
"""
import json
import struct
import subprocess
import zlib
from pathlib import Path

from config import VIDEO_EXTS

# Upper bound on bytes walked through a PNG/WEBP; generated images are a few MB.
MAX_SCAN_BYTES = 64 * 1024 * 1024
# A zTXt/iTXt chunk may not inflate past this (a zip bomb would otherwise do so).
MAX_TEXT_BYTES = 16 * 1024 * 1024
FFPROBE_TIMEOUT = 30

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# --- PNG -----------------------------------------------------------------------

def _inflate(data):
    d = zlib.decompressobj()
    out = d.decompress(data, MAX_TEXT_BYTES)
    if d.unconsumed_tail:
        raise ValueError("text chunk too large")
    return out


def _png_text_chunks(path):
    out = {}
    with open(path, "rb") as f:
        if f.read(8) != PNG_SIGNATURE:
            return out
        scanned = 8
        while scanned < MAX_SCAN_BYTES:
            head = f.read(8)
            if len(head) < 8:
                break
            length, ctype = struct.unpack(">I4s", head)
            if ctype == b"IEND":
                break
            if ctype not in (b"tEXt", b"zTXt", b"iTXt"):
                f.seek(length + 4, 1)  # data + CRC
                scanned += length + 12
                continue
            data = f.read(length)
            f.seek(4, 1)
            scanned += length + 12
            if len(data) < length:
                break
            try:
                key, text = _decode_text_chunk(ctype, data)
            except (ValueError, zlib.error, UnicodeDecodeError):
                continue
            if key:
                out[key] = text
    return out


def _decode_text_chunk(ctype, data):
    key, sep, rest = data.partition(b"\x00")
    if not sep:
        raise ValueError("no keyword terminator")
    key = key.decode("latin-1")
    if ctype == b"tEXt":
        return key, rest.decode("latin-1")
    if ctype == b"zTXt":
        # rest = compression method (1 byte) + zlib stream
        return key, _inflate(rest[1:]).decode("latin-1")
    # iTXt: compression flag, method, language\0, translated keyword\0, text
    if len(rest) < 2:
        raise ValueError("short iTXt")
    compressed = rest[0]
    rest = rest[2:]
    _lang, _, rest = rest.partition(b"\x00")
    _tkey, _, text = rest.partition(b"\x00")
    if compressed:
        text = _inflate(text)
    return key, text.decode("utf-8")


# --- WEBP ----------------------------------------------------------------------

def _webp_exif(path):
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
            return {}
        scanned = 12
        while scanned < MAX_SCAN_BYTES:
            ch = f.read(8)
            if len(ch) < 8:
                return {}
            fourcc, size = struct.unpack("<4sI", ch)
            if fourcc == b"EXIF":
                if size > MAX_TEXT_BYTES:
                    return {}
                return _parse_tiff_ascii(f.read(size))
            f.seek(size + (size & 1), 1)  # chunks are padded to even length
            scanned += 8 + size + (size & 1)
    return {}


def _parse_tiff_ascii(exif):
    """IFD0 ASCII tags of a TIFF blob, split on their first ':' into key → value."""
    if exif.startswith(b"Exif\x00\x00"):
        exif = exif[6:]
    if len(exif) < 8:
        return {}
    order = {b"II": "<", b"MM": ">"}.get(exif[:2])
    if order is None:
        return {}
    try:
        (ifd,) = struct.unpack(order + "I", exif[4:8])
        (count,) = struct.unpack(order + "H", exif[ifd:ifd + 2])
    except struct.error:
        return {}
    out = {}
    for i in range(count):
        entry = exif[ifd + 2 + i * 12: ifd + 14 + i * 12]
        if len(entry) < 12:
            break
        tag, typ, n = struct.unpack(order + "HHI", entry[:8])
        if typ != 2:  # ASCII only
            continue
        if n <= 4:
            raw = entry[8:8 + n]
        else:
            (off,) = struct.unpack(order + "I", entry[8:12])
            raw = exif[off:off + n]
        text = raw.rstrip(b"\x00").decode("utf-8", "replace")
        key, sep, value = text.partition(":")
        if sep and key and " " not in key:
            out[key.lower()] = value
        else:
            out[f"exif_0x{tag:04x}"] = text
    return out


# --- video ---------------------------------------------------------------------

def _video_tags(path):
    cmd = ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFPROBE_TIMEOUT)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}
    tags = (data.get("format") or {}).get("tags") or {}
    out = {str(k).lower(): str(v) for k, v in tags.items()}
    # VideoHelperSuite packs {prompt, workflow, …} into one JSON comment tag.
    comment = out.get("comment")
    if comment:
        try:
            packed = json.loads(comment)
        except ValueError:
            packed = None
        if isinstance(packed, dict):
            del out["comment"]
            for k, v in packed.items():
                out.setdefault(str(k).lower(),
                               v if isinstance(v, str) else json.dumps(v))
    # ffprobe always reports the muxer; it says nothing about the generation.
    for noise in ("major_brand", "minor_version", "compatible_brands", "encoder"):
        out.pop(noise, None)
    return out


def read_raw_metadata(path):
    """{key: text} of whatever generation metadata the file carries ({} if none)."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".png":
            return _png_text_chunks(path)
        if ext == ".webp":
            return _webp_exif(path)
        if ext in VIDEO_EXTS:
            return _video_tags(path)
    except OSError:
        return {}
    return {}


# --- interpretation ------------------------------------------------------------

def _decode_json(text):
    """JSON-decode, twice if the value is a JSON string that itself holds JSON."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, (dict, list)) else None


# Input name → (section, label). Sections order the rows; the client renders them
# in this order. Adding an input to the summary is one line here.
SUMMARY_INPUTS = {
    "text": ("prompt", "Prompt"),
    "prompt": ("prompt", "Prompt"),
    "positive": ("prompt", "Positive"),
    "negative": ("prompt", "Negative"),
    "text_g": ("prompt", "Prompt (G)"),
    "text_l": ("prompt", "Prompt (L)"),
    "seed": ("sampling", "Seed"),
    "noise_seed": ("sampling", "Seed"),
    "steps": ("sampling", "Steps"),
    "cfg": ("sampling", "CFG"),
    "sampler_name": ("sampling", "Sampler"),
    "scheduler": ("sampling", "Scheduler"),
    "denoise": ("sampling", "Denoise"),
    "ckpt_name": ("model", "Checkpoint"),
    "unet_name": ("model", "Diffusion model"),
    "lora_name": ("model", "LoRA"),
    "strength_model": ("model", "LoRA strength"),
    "vae_name": ("model", "VAE"),
    "clip_name": ("model", "CLIP"),
    "clip_name1": ("model", "CLIP 1"),
    "clip_name2": ("model", "CLIP 2"),
    "width": ("size", "Width"),
    "height": ("size", "Height"),
    "length": ("size", "Length"),
    "frame_rate": ("size", "Frame rate"),
}
SECTION_ORDER = ("prompt", "sampling", "model", "size")
SEED_INPUTS = {"seed", "noise_seed"}


def _node_sort_key(node_id):
    # "12", "320:330" — numeric where possible, so 9 sorts before 10.
    return [int(p) if p.isdigit() else 0 for p in str(node_id).split(":")] + [str(node_id)]


def summarise_prompt_graph(prompt):
    """Rows of the interesting literal inputs in an API-format graph."""
    if not isinstance(prompt, dict):
        return []
    rows = []
    for node_id in sorted(prompt, key=_node_sort_key):
        node = prompt[node_id]
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type") or "")
        title = str((node.get("_meta") or {}).get("title") or class_type)
        for name, value in inputs.items():
            spec = SUMMARY_INPUTS.get(name)
            if spec is None or isinstance(value, (list, dict)) or value is None:
                continue  # a [node, slot] link, not a literal
            section, label = spec
            if section == "prompt" and (not isinstance(value, str) or not value.strip()):
                continue
            if name in SEED_INPUTS or isinstance(value, int) and not isinstance(value, bool):
                value = str(value)  # 64-bit seeds don't survive a JS Number
            rows.append({
                "section": section,
                "label": label,
                "value": value if isinstance(value, str) else json.dumps(value),
                "node": str(node_id),
                "class_type": class_type,
                "title": title,
            })
    order = {s: i for i, s in enumerate(SECTION_ORDER)}
    rows.sort(key=lambda r: order[r["section"]])  # stable: node order kept within
    return rows


def parse_metadata(raw):
    """Interpret read_raw_metadata's output for the Inspect table."""
    raw = dict(raw or {})
    prompt_text = raw.pop("prompt", None)
    workflow_text = raw.pop("workflow", None)
    prompt = _decode_json(prompt_text) if prompt_text else None
    workflow = _decode_json(workflow_text) if workflow_text else None
    # A non-JSON "prompt" (some node packs write plain text) is still worth showing.
    if prompt_text and prompt is None:
        raw["prompt"] = prompt_text
    if workflow_text and workflow is None:
        raw["workflow"] = workflow_text
    return {
        "summary": summarise_prompt_graph(prompt),
        "prompt": prompt,
        "workflow": workflow,
        "other": raw,
    }


def inspect_file(path):
    return parse_metadata(read_raw_metadata(path))

