"""
Grok (x.ai) helper — turns a single master prompt into a sequence of detailed
image-generation prompts.

This is a deliberately small, self-contained slice of the standalone
grok-prompt-gen script: one Grok call that returns a JSON array of prompts.
It uses `requests` (already a project dependency) against the OpenAI-compatible
x.ai chat-completions API, so no extra packages are needed.

Configured entirely via environment variables (see docker-compose.yml):
    XAI_API_KEY   — Grok API key (required for /sequence to work)
    GROK_MODEL    — model name (default: grok-4-1-fast-non-reasoning)
    GROK_BASE_URL — API base URL (default: https://api.x.ai/v1)
"""

import os
import json
import requests

GROK_BASE_URL = os.environ.get("GROK_BASE_URL", "https://api.x.ai/v1")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4-1-fast-non-reasoning")
GROK_API_KEY = os.environ.get("XAI_API_KEY", "")


class GrokError(Exception):
    """Raised when the Grok API is unavailable or returns something unusable."""


def grok_available():
    return bool(GROK_API_KEY)


def _chat(messages, temperature=0.8, timeout=120):
    if not GROK_API_KEY:
        raise GrokError("Grok is not configured — set XAI_API_KEY in the environment.")
    try:
        resp = requests.post(
            f"{GROK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": GROK_MODEL, "messages": messages, "temperature": temperature},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise GrokError(f"Could not reach Grok API: {e}")

    if resp.status_code != 200:
        raise GrokError(f"Grok API error {resp.status_code}: {resp.text[:300]}")

    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise GrokError(f"Unexpected Grok API response: {e}")


def generate_prompt_sequence(master_prompt, count):
    """Ask Grok for `count` distinct image prompts derived from `master_prompt`.

    Returns a list of prompt strings. Raises GrokError on any failure.
    """
    system = (
        "You are an expert at writing detailed prompts for photorealistic AI image "
        "generation. You always respond with valid JSON and nothing else."
    )
    user = f"""Based on the following master prompt, create a set of exactly {count} distinct, detailed image-generation prompts.

Master prompt: {master_prompt}

CRITICAL: Each prompt is sent to the image model completely on its own, with no knowledge of the other prompts. There is NO shared context between prompts. Treat every prompt as if it were the only one. This means each prompt MUST:
- Fully restate the subject, scene, and style from scratch — never rely on, refer to, or continue from another prompt
- Never use back-references like "the same woman", "she", "as before", "this time", "again", "now", "continuing", or "the previous scene"; every noun must be introduced fresh as if for the first time
- Stand completely alone and be fully understandable in isolation

Each generated prompt should also:
- Be a single self-contained paragraph describing one specific image
- Keep the core subject, scene, and overall style consistent with the master prompt (by re-describing it in full, not by referring back)
- Vary the pose, composition, camera angle, and small details from prompt to prompt
- Describe a single moment — never multiple poses or sequential actions in one prompt
- Be optimised for a photorealistic text-to-image model

Return ONLY valid JSON in exactly this structure:
{{"prompts": ["first prompt", "second prompt", "..."]}}"""

    content = _chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )

    # Extract the JSON object even if the model wraps it in stray text.
    start = content.find("{")
    end = content.rfind("}") + 1
    if start == -1 or end <= start:
        raise GrokError("Grok did not return JSON.")

    try:
        data = json.loads(content[start:end])
    except json.JSONDecodeError as e:
        raise GrokError(f"Grok returned invalid JSON: {e}")

    prompts = [p.strip() for p in data.get("prompts", []) if isinstance(p, str) and p.strip()]
    if not prompts:
        raise GrokError("Grok returned no usable prompts.")

    return prompts
