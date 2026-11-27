#!/usr/bin/env python3
"""Check a comfy-chatbot workflow template against the app's own workflow.py code.

Usage (from the comfy-chatbot repo root):
    python3 .claude/skills/adapt-workflow/scripts/validate_template.py TEMPLATE [--image]

Exits non-zero if anything would break at generation time.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, REPO)
import workflow as w  # noqa: E402


def links(wf):
    for nid, node in wf.items():
        for key, val in node.get("inputs", {}).items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                yield nid, key, val[0]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    text = open(args[0]).read()
    problems = []

    try:
        json.loads(w.fill_placeholders_for_validation(text))
        print("ok    parses with validation dummies")
    except json.JSONDecodeError as e:
        sys.exit(f"FAIL  does not parse with validation dummies: {e}")

    tokens = w.find_placeholders(text)
    print("info  tokens:", ", ".join(tokens) or "(none)")
    if "<PROMPT>" not in tokens and "--image" not in sys.argv:
        problems.append("no <PROMPT> token")

    mapping = {"PROMPT": 'a "quoted" \\ test prompt',
               "LORA_1_NAME": "test.safetensors", "LORA_1_STRENGTH": 0.8,
               "DENOISE": 0.5, "WIDTH": 1024, "HEIGHT": 1024,
               "DURATION": 5, "FRAMES": 121, "FPS": 24,
               "VIDEO_WIDTH": 1280, "VIDEO_HEIGHT": 720, "LAST_FRAME_STRENGTH": 0.0}
    if "--image" in sys.argv:
        mapping.update({"INPUT_IMAGE": "in.png", "INPUT_MASK": "mask.png",
                        "INPUT_LAST_FRAME": "in.png", "REFERENCE_IMAGE_1": "in.png"})
    filled = w.fill_lora_sentinels(w.apply_placeholders(text, mapping))
    try:
        wf = json.loads(filled)
    except json.JSONDecodeError as e:
        sys.exit(f"FAIL  does not parse after filling: {e}")
    w.strip_lora_nodes(wf)

    left = w.find_placeholders(json.dumps(wf))
    if left:
        problems.append(f"unfilled placeholders (needs a real value or stripping): {left}")
    for nid, key, target in links(wf):
        if target not in wf:
            problems.append(f"dangling link {nid}.{key} -> {target}")

    loras = [k for k, v in wf.items() if "Lora" in v.get("class_type", "")]
    print(f"info  LoRA nodes after strip (1 slot filled): {loras}")
    for nid, key, target in links(wf):
        if target in loras:
            print(f"info    {nid}.{key} <- {target}")

    w.apply_resolution(wf, 832, 1216)
    sized = {k: (v["inputs"]["width"], v["inputs"]["height"]) for k, v in wf.items()
             if "width" in v.get("inputs", {}) and "height" in v.get("inputs", {})}
    print(f"info  nodes resized by apply_resolution(832x1216): {sized or 'none'}")
    print(f"info  seeds found: {w.collect_seeds(wf) or 'none (seed will not randomise)'}")

    for p in problems:
        print("FAIL ", p)
    if problems:
        sys.exit(1)
    print("ok    template is usable by the app (not yet test-rendered in ComfyUI)")


if __name__ == "__main__":
    main()
