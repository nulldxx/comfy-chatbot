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
import re
import json
import logging
import requests

log = logging.getLogger(__name__)

GROK_BASE_URL = os.environ.get("GROK_BASE_URL", "https://api.x.ai/v1")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4-1-fast-non-reasoning")
# The primary model occasionally serves corrupt output (see _parse_prompts).
# When that happens we retry once with this fallback, then fall back to the
# primary automatically again as soon as it recovers server-side.
GROK_FALLBACK_MODEL = os.environ.get("GROK_FALLBACK_MODEL", "grok-4-1-fast-reasoning")
GROK_API_KEY = os.environ.get("XAI_API_KEY", "")

# Matches a leaked model special token such as <|eos|> or <|separator|>. These
# should never appear in a well-formed reply; their presence means the model
# corrupted its own output and the JSON is unreliable.
_SPECIAL_TOKEN_RE = re.compile(r"<\|\w+\|>")


class GrokError(Exception):
    """Raised when the Grok API is unavailable or returns something unusable."""


def grok_available():
    return bool(GROK_API_KEY)


# Keep this safely below gunicorn's worker `timeout` (120s). If the HTTP call
# is allowed to run as long as the worker timeout, a slow Grok response races
# the worker kill and the client receives a non-JSON body (gunicorn error page)
# instead of a clean {"error": ...} — surfacing as a "JSON.parse: unexpected
# character at line 1 column 1" in the browser.
def _chat(messages, temperature=0.8, timeout=90, max_tokens=None, model=None):
    if not GROK_API_KEY:
        raise GrokError("Grok is not configured — set XAI_API_KEY in the environment.")
    payload = {"model": model or GROK_MODEL, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    try:
        resp = requests.post(
            f"{GROK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
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

    # Each detailed prompt is a full paragraph (~300 tokens). Budget generously
    # per prompt plus a fixed overhead so the JSON array is never cut off
    # mid-string — a truncated response has no closing brace and fails parsing.
    max_tokens = 1024 + count * 500
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Try the preferred model first; if it serves corrupt/unparseable output
    # (e.g. grok-4-1-fast-non-reasoning has been observed leaking <|eos|> tokens
    # and returning broken JSON), retry once with the reasoning fallback. We
    # keep the primary as first choice so the app reverts to it automatically
    # once it's healthy again, without a config change or redeploy.
    models = [GROK_MODEL]
    if GROK_FALLBACK_MODEL and GROK_FALLBACK_MODEL != GROK_MODEL:
        models.append(GROK_FALLBACK_MODEL)

    last_error = None
    for model in models:
        try:
            content = _chat(messages, max_tokens=max_tokens, model=model)
            return _parse_prompts(content)
        except GrokError as e:
            last_error = e
            log.warning("Grok model %s failed for /sequence: %s", model, e)

    raise last_error or GrokError("Grok is not configured — no model to try.")


def _parse_prompts(content):
    """Extract the prompts list from a Grok JSON reply, or raise GrokError."""
    # A leaked special token means the model corrupted its own output; the JSON
    # may even parse but cannot be trusted, so reject it (triggers a fallback).
    if _SPECIAL_TOKEN_RE.search(content or ""):
        raise GrokError(
            f"Grok returned a corrupt response (leaked a special token) — "
            f"model said: {(content or '').strip()[:300]}"
        )

    # Extract the JSON object even if the model wraps it in stray text.
    start = content.find("{")
    end = content.rfind("}") + 1
    if start == -1 or end <= start:
        snippet = (content or "").strip()[:300] or "<empty response>"
        raise GrokError(f"Grok did not return JSON — model said: {snippet}")

    try:
        data = json.loads(content[start:end])
    except json.JSONDecodeError as e:
        raise GrokError(f"Grok returned invalid JSON: {e} — model said: {content[start:end][:300]}")

    prompts = [p.strip() for p in data.get("prompts", []) if isinstance(p, str) and p.strip()]
    if not prompts:
        raise GrokError("Grok returned no usable prompts.")

    return prompts
