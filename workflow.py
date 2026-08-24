import re
import json
import random

PLACEHOLDER_RE = re.compile(r"<[A-Z0-9_]+>")
LORA_PLACEHOLDER_RE = re.compile(r"<LORA_\d+_(?:NAME|STRENGTH)>")
LORA_NAME_SENTINEL = "__LORA_UNSET__"
LORA_TAG_RE = re.compile(r'<lora:([^:>\s]+)(?::([0-9.]+))?>', re.IGNORECASE)


def reference_sentinel(token):
    """Sentinel filename substituted for an unfilled optional reference placeholder.

    An unfilled non-LoRA placeholder is a hard error, so a reference slot that the
    user didn't supply (e.g. <REFERENCE_VIDEO> with no reference video) is filled with
    this sentinel string so the template parses, then its loader node is removed by
    strip_reference_nodes. ``token`` is the placeholder name without angle brackets.
    """
    return f"__REF_UNSET_{token}__"


def reference_marker(token):
    """Locator string substituted for a reference placeholder whose node we must find.

    Used by the single-node video-track convention: one VHS "Load Video (Upload)" node
    holds <REFERENCE_VIDEO_n> and drives both an IMAGE and an AUDIO consumer input, so
    turning one track off means dropping that output's links (drop_node_output_links)
    rather than deleting the node. To do that we must identify the loader node, and the
    uploaded filename alone is ambiguous — the same clip can sit in two slots with
    different track flags. Substituting this per-token marker instead makes the node
    unique by construction; the real filename is written back immediately after the
    JSON is parsed (see find_marked_node). ``token`` is the name without angle brackets.
    """
    return f"__REF_NODE_{token}__"


def find_marked_node(workflow, marker):
    """Locate the node holding ``marker`` as an input value; return (node_id, key).

    Returns (None, None) when the marker isn't present — the node may legitimately
    have been removed already (e.g. by strip_reference_nodes).
    """
    for nid, node in workflow.items():
        for key, value in node.get("inputs", {}).items():
            if value == marker:
                return nid, key
    return None, None


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

    # Collect candidate upstream nodes reachable from the guide's private inputs.
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

    # Only delete nodes that are exclusively referenced within the removal set.
    # Shared nodes (e.g. Width/Height primitives used by both the guide resize
    # and the main resize) must not be removed.
    def _referenced_outside(nid):
        return any(
            isinstance(v, list) and len(v) == 2 and v[0] == nid
            for other_id, other in workflow.items()
            if other_id not in to_remove
            for v in other.get("inputs", {}).values()
        )

    safe_to_remove = {nid for nid in to_remove if not _referenced_outside(nid)}

    del workflow[guide_id]
    _rewire_references(workflow, guide_id, passthrough)

    for nid in safe_to_remove - {guide_id}:
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


def strip_reference_nodes(workflow, sentinels):
    """Remove loader nodes for unfilled optional reference slots and drop consumers.

    ``sentinels`` is the set of sentinel filename strings (see reference_sentinel)
    substituted for reference placeholders the user didn't supply. Any node holding
    one of these as an input value is a reference loader with no real file, so we:
      1. delete that loader node, and
      2. for every OTHER node whose input connects to the deleted loader, delete that
         input key entirely — a reference feeds an *optional* input on the consumer
         (e.g. the MiniMax node's image_2 / ref_video / ref_audio), and the correct
         ComfyUI semantics for "no file supplied" is an absent optional input, not a
         passthrough rewire (there is no upstream value to pass through).

    Returns (workflow, removed_ids).
    """
    if not sentinels:
        return workflow, []
    sentinel_set = set(sentinels)
    removed = [
        nid for nid, node in workflow.items()
        if any(v in sentinel_set for v in node.get("inputs", {}).values()
               if isinstance(v, str))
    ]
    for nid in removed:
        del workflow[nid]
    # Drop any connection referencing a removed loader (an optional input goes absent).
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in [k for k, v in inputs.items()
                    if isinstance(v, list) and len(v) == 2 and v[0] in removed]:
            del inputs[key]
    return workflow, removed


def drop_node_output_links(workflow, node_id, output_indices):
    """Delete every consumer input wired to one of ``node_id``'s given outputs.

    Unlike strip_reference_nodes the producer node SURVIVES: this is how a single
    loader that emits several tracks (VHS Load Video: IMAGE + AUDIO) has just one of
    them disconnected, which is what unticking one of a video slot's two track boxes
    means. As there, the right semantics for "not supplied" is an absent optional
    input on the consumer, not a passthrough rewire.

    Returns [(consumer_id, input_key), ...] for the links removed.
    """
    wanted = set(output_indices)
    removed = []
    if not wanted:
        return removed
    for cid, node in workflow.items():
        inputs = node.get("inputs", {})
        for key in [k for k, v in inputs.items()
                    if isinstance(v, list) and len(v) == 2
                    and v[0] == node_id and v[1] in wanted]:
            del inputs[key]
            removed.append((cid, key))
    return removed


def node_link_output_indices(workflow, node_id, name_contains="audio"):
    """Split the output indices ``node_id`` actually drives, by consumer input name.

    Fallback classifier for when the server's declared output types aren't available
    (see ComfyServer.get_node_output_types): a consumer input whose key contains
    ``name_contains`` is taken to be the audio link, everything else the video link.
    Returns (matching, other) as sorted lists of output indices.
    """
    needle = name_contains.lower()
    matching, other = set(), set()
    for node in workflow.values():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and value[0] == node_id:
                (matching if needle in key.lower() else other).add(value[1])
    return sorted(matching), sorted(other)


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


def apply_seed(workflow, seed):
    """Set every seed/noise_seed input in an API-format workflow to a fixed value.

    Used to reproduce a previous generation (see /getseed): a single scalar seed is
    applied to all sampler nodes, so a single-sampler workflow reproduces exactly and
    a multi-sampler one shares the one seed. Returns the count of inputs set."""
    applied = 0
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            if isinstance(inputs.get(key), (int, float)):
                inputs[key] = seed
                applied += 1
    return applied


def collect_seeds(workflow):
    """Return the list of integer seed/noise_seed values currently in the workflow."""
    seeds = []
    for node in workflow.values():
        inputs = node.get("inputs", {})
        for key in ("seed", "noise_seed"):
            value = inputs.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                seeds.append(value)
    return seeds


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
