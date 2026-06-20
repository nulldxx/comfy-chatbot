import re
import json

from config import (
    COMFY_WORKFLOW_DIR, COMFY_LORAS_FILE,
    COMFY_FACEDETAILER_DIR,
    COMFY_UPSCALER_DIR, COMFY_IMAGE2IMAGE_DIR, COMFY_INPAINTING_DIR,
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


def load_loras():
    if not COMFY_LORAS_FILE.is_file():
        return []
    try:
        data = json.loads(COMFY_LORAS_FILE.read_text())
        loras = data.get("loras", [])
        return [
            entry if isinstance(entry, dict) else {"name": entry, "strength": 1.0}
            for entry in loras
        ]
    except Exception:
        return []


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
