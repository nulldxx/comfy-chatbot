import re
import json

from config import (
    COMFY_WORKFLOW_DIR, COMFY_LORAS_FILE,
    COMFY_FACEDETAILER_DIR,
    COMFY_UPSCALER_DIR, COMFY_IMAGE2IMAGE_DIR, COMFY_INPAINTING_DIR,
    COMFY_IMAGE2VIDEO_DIR, COMFY_REMOVAL_DIR,
)
from workflow import LORA_TAG_RE


def load_server_catalogue():
    servers_file = COMFY_WORKFLOW_DIR / "servers.json"
    if not servers_file.is_file():
        return []
    try:
        return json.loads(servers_file.read_text()).get("servers", [])
    except Exception:
        return []


def parse_strength(value, default=0.8):
    """Coerce a suggested_strength into a float.

    Accepts a number, a numeric string (``"0.8"``), or a hyphenated range
    (``"0.8-1.2"``), which resolves to the average of its endpoints. Falsy
    values (None, ``""``, 0) fall back to ``default``. Raises ``ValueError``
    on anything unparseable.
    """
    if not value:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        pass
    m = re.match(r'^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$', text)
    if m:
        return round((float(m.group(1)) + float(m.group(2))) / 2, 4)
    raise ValueError(f"invalid strength value: {value!r}")


def load_loras_result():
    """Load the LoRA catalogue, returning ``{"loras": [...], "error": str|None}``.

    A missing file is not an error — LoRAs are optional. A present-but-broken
    file (unreadable, not valid JSON, or an entry with an unparseable
    ``suggested_strength``) yields whatever entries did parse plus a
    human-readable error for the UI to surface at session start. Individual
    bad entries are skipped rather than wiping out the whole catalogue.
    """
    if not COMFY_LORAS_FILE.is_file():
        return {"loras": [], "error": None}
    try:
        data = json.loads(COMFY_LORAS_FILE.read_text())
    except Exception as exc:
        return {"loras": [], "error": f"Could not read LoRA file {COMFY_LORAS_FILE.name}: {exc}"}
    if not isinstance(data, dict):
        return {"loras": [], "error": f"LoRA file {COMFY_LORAS_FILE.name} must be a JSON object of name → metadata."}

    loras, bad = [], []
    for name, meta in data.items():
        meta = meta if isinstance(meta, dict) else {}
        try:
            strength = parse_strength(meta.get("suggested_strength"))
        except (ValueError, TypeError):
            bad.append(f"{name} ({meta.get('suggested_strength')!r})")
            continue
        loras.append({
            "name": name,
            "strength": strength,
            "triggers": meta.get("active_triggers") or "",
        })

    error = None
    if bad:
        error = f"{len(bad)} LoRA(s) had an invalid suggested_strength and were skipped: {', '.join(bad)}"
    return {"loras": loras, "error": error}


def load_loras():
    return load_loras_result()["loras"]


def lora_catalogue_strength(name):
    for entry in load_loras():
        if entry.get("name") == name:
            return str(entry["strength"])
    return None


def parse_loras_from_prompt(text):
    """Strip <lora:name> / <lora:name:strength> tags and return (clean_prompt, [(name, strength)])."""
    loras = []

    def replacer(m):
        name = m.group(1)
        strength = m.group(2) or lora_catalogue_strength(name) or "1.0"
        loras.append((name, strength))
        return ""

    clean = LORA_TAG_RE.sub(replacer, text).strip()
    # Collapse any double-spaces left by removed tags
    clean = re.sub(r'  +', ' ', clean)
    return clean, loras


def list_workflow_names(base_dir):
    """Relative '/'-joined workflow names (no .json) found recursively under base_dir."""
    if not base_dir.is_dir():
        return []
    return sorted(
        f.relative_to(base_dir).with_suffix("").as_posix()
        for f in base_dir.glob("**/*.json")
    )


def list_facedetailer_workflows():
    return list_workflow_names(COMFY_FACEDETAILER_DIR)


def list_upscaler_workflows():
    return list_workflow_names(COMFY_UPSCALER_DIR)


def list_image2image_workflows():
    return list_workflow_names(COMFY_IMAGE2IMAGE_DIR)


def list_inpainting_workflows():
    return list_workflow_names(COMFY_INPAINTING_DIR)


def list_image2video_workflows():
    return list_workflow_names(COMFY_IMAGE2VIDEO_DIR)


def list_removal_workflows():
    return list_workflow_names(COMFY_REMOVAL_DIR)


def resolve_workflow(workflow_name, available, kind):
    """Return (resolved_name, None) or (None, error_tuple) after validating against an allowlist."""
    from flask import jsonify
    if workflow_name:
        name = workflow_name[:-5] if workflow_name.endswith(".json") else workflow_name
        if name not in available:
            return None, (jsonify({"error": f"Unknown {kind} workflow: {workflow_name}"}), 400)
        return name, None
    elif available:
        return available[0], None
    else:
        return None, (jsonify({"error": f"No {kind} workflows available"}), 400)
