"""Server-side ports of the client's pure prompt builders (static/js/utils.js).

A server-driven sequence run (generation_service.run_sequence_run) can face-detail and
image2video each shot with no browser attached, so it has to assemble the same prompts
the client would. Each function here mirrors its utils.js namesake line for line — change
them together, and keep tests/test_prompt_builders.py in step with tests/js/utils.test.js.
"""
import re

# deriveFaceDetailPrompt's two patterns, verbatim.
SUBJECT_RE = re.compile(r"\b(woman|man|girl|boy|lady)\b", re.IGNORECASE)
# Multi-word / hyphenated forms first so e.g. "open mouth" wins over a bare word.
EXPRESSION_RE = re.compile(
    r"\b(open[- ]mouthed|open mouth|wide[- ]eyed|teary[- ]eyed|gritted teeth|clenched teeth|furrowed brow|raised eyebrows?|tongue out|biting lip|lip bite|pursed lips|puppy eyes|side[- ]eye|rolling eyes|eyes closed|closed eyes|head tilt|smiling|smile|grinning|grin|laughing|laugh|chuckling|giggling|beaming|smirking|smirk|winking|wink|frowning|frown|scowling|scowl|pouting|pout|crying|sobbing|weeping|tearful|sniffling|screaming|scream|shouting|yelling|yawning|sneering|snarling|grimacing|gasping|blushing|flushed|surprised|shocked|astonished|amazed|stunned|angry|furious|enraged|rage|annoyed|irritated|sad|sorrowful|melancholy|depressed|gloomy|happy|joyful|joy|cheerful|delighted|gleeful|ecstatic|ecstasy|euphoric|blissful|content|terrified|scared|fearful|afraid|frightened|horrified|panicked|worried|anxious|nervous|confused|puzzled|perplexed|disgusted|disgust|contempt|bored|tired|sleepy|exhausted|serious|stern|solemn|calm|serene|peaceful|relaxed|seductive|flirtatious|sultry|coy|smug|mischievous|playful|determined|focused|concentrating|pained|anguished|agony|suffering|embarrassed|ashamed|shy|bashful|hopeful|longing|yearning|dreamy|thoughtful|pensive|suspicious|skeptical|disappointed|frustrated|desperate|hysterical|manic|deadpan|expressionless|neutral|intense|fierce|menacing)\b",
    re.IGNORECASE,
)
LORA_TAG_RE = re.compile(r"<lora:[^>]+>", re.IGNORECASE)

I2VA_INSTRUCTION = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced."
)


def apply_replacements(prompt, replacements):
    """Plain substring find→replace pairs; a falsy prompt passes through unchanged."""
    if not prompt:
        return prompt
    for src, dst in replacements or []:
        if src:
            prompt = prompt.replace(src, dst)
    return prompt


def derive_face_detail_prompt(gen_prompt):
    """A face-detail prompt from a generation prompt: subject phrase, facial
    expressions and the prompt's <lora:…> tags. None if there is no LoRA tag."""
    if not gen_prompt:
        return None
    lora_tags = LORA_TAG_RE.findall(gen_prompt)
    if not lora_tags:
        return None
    m = SUBJECT_RE.search(gen_prompt)
    subject = f"a {m.group(1).lower()}'s face" if m else "a face"
    expressions = list(dict.fromkeys(s.lower() for s in EXPRESSION_RE.findall(gen_prompt)))
    desc = ", ".join([subject, *expressions])
    return f"{desc} {' '.join(lora_tags)}"


def normalize_video_meta(meta, base=""):
    """Read video metadata in the H3 shape or the legacy { action, audio } one."""
    def t(v):
        return v.strip() if isinstance(v, str) else ""

    if not meta:
        return {"description": "", "soundscape": "", "music": ""}
    if "description" in meta or "soundscape" in meta or "music" in meta:
        return {"description": t(meta.get("description")),
                "soundscape": t(meta.get("soundscape")),
                "music": t(meta.get("music"))}
    action = t(meta.get("action"))
    return {
        "description": ". ".join(p for p in (t(base), action) if p) if action else "",
        "soundscape": t(meta.get("audio")),
        "music": "",
    }


def build_video_prompt(base, meta, audio=True):
    """The MiniMax H3 I2VA image2video prompt (buildVideoPrompt with no end frame)."""
    m = normalize_video_meta(meta, base)
    description = m["description"] or (base or "").strip()
    lines = [I2VA_INSTRUCTION, f"integrated_multimodal_description: [Shot 1] {description}"]
    if not audio:
        lines += ["overall_soundscape: N/A", "non_diegetic_music: N/A"]
    else:
        if m["soundscape"]:
            lines.append(f"overall_soundscape: {m['soundscape']}")
        lines.append(f"non_diegetic_music: {m['music'] or 'N/A'}")
    return "\n\n".join(lines)
