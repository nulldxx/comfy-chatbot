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
def _chat(messages, temperature=0.8, timeout=90, max_tokens=None, model=None, session=None):
    if not GROK_API_KEY:
        raise GrokError("Grok is not configured — set XAI_API_KEY in the environment.")
    payload = {"model": model or GROK_MODEL, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    # When a session is supplied the caller can abort an in-flight request from
    # another thread by closing it (see the /sequence cancel path); a bare
    # requests.post() cannot be interrupted that way.
    http = session or requests
    try:
        resp = http.post(
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


def generate_prompt_sequence(master_prompt, count, cancel_event=None, session=None):
    """Ask Grok for `count` distinct image prompts derived from `master_prompt`.

    Returns a list of prompt strings. Raises GrokError on any failure.

    Pass `session` (a requests.Session) and `cancel_event` (a threading.Event)
    to make the call cancellable: closing the session aborts the in-flight HTTP
    request, and a set event stops us from trying the fallback model.
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
        if cancel_event is not None and cancel_event.is_set():
            raise GrokError("Cancelled")
        try:
            content = _chat(messages, max_tokens=max_tokens, model=model, session=session)
            return _parse_prompts(content)
        except GrokError as e:
            last_error = e
            log.warning("Grok model %s failed for /sequence: %s", model, e)

    raise last_error or GrokError("Grok is not configured — no model to try.")


def generate_video_prompt_sequence(master_prompt, count, cancel_event=None, session=None):
    """Ask Grok for `count` distinct video shots derived from `master_prompt`.

    Like generate_prompt_sequence, but each item also carries the fields of a
    MiniMax H3 video prompt (docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md in the
    MiniMaxAI/MiniMax-H3 repo):
      - prompt:      the self-contained still-image prompt (used for image generation)
      - description: the integrated_multimodal_description body — a short account of
                     what happens next (action, reaction, dialogue in <d> tags). It
                     deliberately does NOT restate the still's appearance, setting or
                     style: H3 is given the still as its first frame, and re-describing
                     it crowded out the action.
      - soundscape:  overall_soundscape — ambient, physical and non-verbal sound
      - music:       always "N/A" — the score is left to the user (metadata editor)

    The instruction line and "[Shot 1]" are added client-side (buildVideoPrompt in
    utils.js), because only the client knows whether an end frame is designated and
    how long the clip is.

    Returns a list of dicts {"prompt", "description", "soundscape", "music"}.
    Raises GrokError on any failure.
    """
    system = (
        "You are an expert at writing detailed prompts for photorealistic AI image "
        "and video generation. You always respond with valid JSON and nothing else."
    )
    user = f"""Based on the following master prompt, create a set of exactly {count} distinct video shots.

Each shot is made in two stages: a still image is generated from the shot's "prompt", then that still becomes the first frame of a short (about 5 second) video clip generated by the MiniMax H3 video model from the shot's "description" and "soundscape". The video model SEES the still, so it already knows what everyone looks like, what they wear, where they are, the lighting and the style. The video text only has to say what happens next.

Master prompt: {master_prompt}

For EACH shot, produce three pieces of text:
1. "prompt" — a detailed still-image generation prompt describing a single frame of the shot.
2. "description" — the video prompt for that clip: what happens next, starting from the still.
3. "soundscape" — the ambient and physical sound of that clip.

CRITICAL: Each "prompt" is sent to the image model completely on its own, with no knowledge of the other shots. There is NO shared context between shots. Treat every shot as if it were the only one. This means each "prompt" MUST:
- Fully restate the subject, scene, and style from scratch — never rely on, refer to, or continue from another shot
- Never use back-references like "the same woman", "she", "as before", "this time", "again", "now", "continuing", or "the previous scene"; every noun must be introduced fresh as if for the first time
- Stand completely alone and be fully understandable in isolation
- Include every person the master prompt implies is present (e.g. "they sit together" means at least two people)

Each "prompt" should also:
- Be a single self-contained paragraph describing one specific image
- Keep the core subject, scene, and overall style consistent with the master prompt (by re-describing it in full, not by referring back)
- Vary the pose, composition, camera angle, and small details from shot to shot
- Describe a single moment — never multiple poses or sequential actions in one prompt
- Be optimised for a photorealistic text-to-image model

Each "description" is sent to the video model on its own, alongside the still. It is NOT an image prompt. Each "description" MUST:
- Be short: 1 to 3 plain English sentences describing ONE continuous shot with no cuts, sized for about 5 seconds — one main action and its result, and at most one or two short lines of dialogue
- Focus on what happens NEXT: the movement, the action and its result, the reaction, and above all anything that is said
- Refer to people simply ("the man", "the woman", "the older man on the left") — NEVER describe their appearance, age, hair, clothing, the room, the furniture, the lighting, the art style, the lens or the focus; the still already shows all of that
- When the master prompt implies speech (someone says, asks, tells, replies, shouts, sings...), write the actual words: invent natural, specific lines that fit the scene, and let the dialogue carry the shot
- Give anyone who speaks or sings a speaker ID, (S1) then (S2), placed right after a short phrase identifying them and their voice, e.g. "the man, in a deep voice (S1)". Characters who never speak get no ID. Put the EXACT spoken words, verbatim, inside <d>[English] ...</d> with nothing else inside the tags (use the tag of the language actually spoken)
- For a voiceover, write "says in an off-screen voiceover:" and follow the <d> block with "while his lips remain completely closed" (or her / their)
- Mention camera movement only if it matters, as a short natural phrase (e.g. "the camera pushes in slowly"); otherwise say nothing about the camera
- Describe only things that can be seen or heard, never abstract mood
- NOT include "[Shot 1]", timestamps, cuts, or any "Picture 1" / first-frame instruction — those are added automatically
- NOT describe music

Each "soundscape" MUST:
- Be 1 to 3 English sentences in one paragraph covering ambient sound, physical action sounds and non-verbal human sounds (wind, rain, traffic, a TV, footsteps, fabric, impacts, breathing, laughter)
- Never repeat dialogue, singing or music — those belong in "description" or are left to the user
- Describe observable sound, never mood, and never be "N/A"

Example of a good description and soundscape for a still of a man and a woman on a sofa watching football on TV:
description: The man, in a relaxed, low voice (S1), turns to the woman and says: <d>[English] Do you reckon Spurs are going to win?</d> She shrugs and the woman, in a dry, amused voice (S2), replies: <d>[English] Nah, I don't think they will.</d>
soundscape: A faint television broadcast murmur with crowd noise is audible from the screen. Subtle fabric rustle occurs as he shifts his weight on the sofa cushions.

Return ONLY valid JSON in exactly this structure:
{{"prompts": [{{"prompt": "...", "description": "...", "soundscape": "..."}}]}}"""

    # Each item is a full image paragraph (~300 tokens) plus a short description
    # and soundscape. Budget generously so the JSON array is
    # never cut off mid-string — a truncated response fails parsing.
    max_tokens = 1024 + count * 800
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    models = [GROK_MODEL]
    if GROK_FALLBACK_MODEL and GROK_FALLBACK_MODEL != GROK_MODEL:
        models.append(GROK_FALLBACK_MODEL)

    last_error = None
    for model in models:
        if cancel_event is not None and cancel_event.is_set():
            raise GrokError("Cancelled")
        try:
            content = _chat(messages, max_tokens=max_tokens, model=model, session=session)
            return _parse_video_prompts(content)
        except GrokError as e:
            last_error = e
            log.warning("Grok model %s failed for /video-sequence: %s", model, e)

    raise last_error or GrokError("Grok is not configured — no model to try.")


def _extract_json_object(content):
    """Reject leaked special tokens and return the parsed top-level JSON object.

    Shared by _parse_prompts and _parse_video_prompts. Raises GrokError if the
    response is corrupt, contains no JSON object, or is not valid JSON.
    """
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
        return json.loads(content[start:end])
    except json.JSONDecodeError as e:
        raise GrokError(f"Grok returned invalid JSON: {e} — model said: {content[start:end][:300]}")


def _parse_video_prompts(content):
    """Extract the list of {prompt, description, soundscape, music} dicts from a Grok reply.

    Music is always "N/A": background score is left to the user, so anything the
    model volunteers is ignored. A missing description or soundscape is left empty.
    """
    data = _extract_json_object(content)

    def text(value):
        return value.strip() if isinstance(value, str) else ""

    out = []
    for item in data.get("prompts", []):
        if not isinstance(item, dict):
            continue
        prompt = text(item.get("prompt"))
        if not prompt:
            continue
        out.append({
            "prompt": prompt,
            "description": text(item.get("description")),
            "soundscape": text(item.get("soundscape")),
            "music": "N/A",
        })

    if not out:
        raise GrokError("Grok returned no usable video prompts.")

    return out


def _parse_prompts(content):
    """Extract the prompts list from a Grok JSON reply, or raise GrokError."""
    data = _extract_json_object(content)

    prompts = [p.strip() for p in data.get("prompts", []) if isinstance(p, str) and p.strip()]
    if not prompts:
        raise GrokError("Grok returned no usable prompts.")

    return prompts
