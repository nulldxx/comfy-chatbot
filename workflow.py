import re
import json
import random

PLACEHOLDER_RE = re.compile(r"<[A-Z0-9_]+>")
LORA_PLACEHOLDER_RE = re.compile(r"<LORA_\d+_(?:NAME|STRENGTH)>")
LORA_NAME_SENTINEL = "__LORA_UNSET__"
LORA_TAG_RE = re.compile(r'<lora:([^:>\s]+)(?::([0-9.]+))?>', re.IGNORECASE)


def apply_placeholders(text, mapping):
    for key, value in mapping.items():
        escaped = json.dumps(str(value))[1:-1]
        text = text.replace(f"<{key}>", escaped)
    return text


def find_placeholders(text):
    return sorted(set(PLACEHOLDER_RE.findall(text)))


def fill_lora_sentinels(text):
    text = re.sub(r"<LORA_\d+_NAME>", LORA_NAME_SENTINEL, text)
    text = re.sub(r"<LORA_\d+_STRENGTH>", "0", text)
    return text


def strip_last_frame_guide(workflow):
    """Remove the LTXVAddGuide last-frame chain when no end frame is provided.

    Strength=0.0 is not a true no-op — the guide still embeds the image into the
    latent at the last position, causing a snap-back transition. Removing the nodes
    entirely and rewiring around them is the correct bypass.
    """
    guide_id = next(
        (nid for nid, n in workflow.items() if n.get("class_type") == "LTXVAddGuide"),
        None,
    )
    if guide_id is None:
        return workflow

    inputs = workflow[guide_id].get("inputs", {})
    passthrough = {
        0: inputs.get("positive"),  # positive conditioning
        1: inputs.get("negative"),  # negative conditioning
        2: inputs.get("latent"),    # video latent
    }

    # Collect upstream-only nodes to delete (they feed only into the guide chain)
    to_remove = {guide_id}

    def _trace(ref):
        if isinstance(ref, list) and len(ref) == 2:
            nid = ref[0]
            if nid in workflow and nid not in to_remove:
                to_remove.add(nid)
                for v in workflow[nid].get("inputs", {}).values():
                    _trace(v)

    _trace(inputs.get("image"))    # preprocess → resize → load_last_frame chain
    _trace(inputs.get("strength")) # strength primitive

    del workflow[guide_id]
    _rewire_references(workflow, guide_id, passthrough)

    for nid in to_remove - {guide_id}:
        workflow.pop(nid, None)

    return workflow


def strip_lora_nodes(workflow):
    removed = [
        node_id
        for node_id, node in workflow.items()
        if node.get("inputs", {}).get("lora_name") == LORA_NAME_SENTINEL
    ]
    for node_id in removed:
        inputs = workflow[node_id].get("inputs", {})
        passthrough = {0: inputs.get("model")}
        if "clip" in inputs:
            passthrough[1] = inputs.get("clip")
        del workflow[node_id]
        _rewire_references(workflow, node_id, passthrough)
    return workflow, removed


def _rewire_references(workflow, removed_id, passthrough):
    for node in workflow.values():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and value[0] == removed_id:
                replacement = passthrough.get(value[1])
                if replacement is not None:
                    node["inputs"][key] = replacement


def randomize_seeds(workflow):
    """Replace every seed/noise_seed input in an API-format workflow with a random value."""
    randomized = 0
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            if isinstance(inputs.get(key), (int, float)):
                inputs[key] = random.randint(0, 2**64 - 1)
                randomized += 1
    return randomized


def lora_path_for_os(path, os_type):
    if os_type == "windows":
        return path.replace("/", "\\")
    return path


def apply_resolution(workflow, width, height):
    """Set width/height on every workflow node that exposes both as inputs."""
    for node in workflow.values():
        inputs = node.get("inputs", {})
        if "width" in inputs and "height" in inputs:
            inputs["width"] = width
            inputs["height"] = height


def apply_steps(workflow, steps):
    """Set steps on every workflow node that exposes it as an input."""
    for node in workflow.values():
        inputs = node.get("inputs", {})
        if "steps" in inputs:
            inputs["steps"] = steps


def fill_placeholders_for_validation(text):
    """Replace template tokens with dummy values so the file parses as JSON."""
    text = re.sub(r"<LORA_\d+_STRENGTH>", "1.0", text)   # unquoted numeric slots
    text = re.sub(r"<DENOISE>", "1.0", text)              # unquoted numeric slot
    text = re.sub(r"<(?:DURATION|FRAMES|FPS|VIDEO_WIDTH|VIDEO_HEIGHT)>", "1", text)  # unquoted numeric video slots
    text = re.sub(r"<LAST_FRAME_STRENGTH>", "1.0", text)  # unquoted float slot (image2video guide)
    text = re.sub(r"<[A-Z0-9_]+>", "placeholder", text)   # all remaining string slots
    return text
