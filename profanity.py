"""
Optional profanity filter for the prompts that leave the box.

Configured entirely by the PROFANITY_FILTER environment variable (see
docker-compose.yml): a comma-separated list of banned words. Unset or empty —
the default — disables the filter completely, so an existing deployment behaves
exactly as it did.

Scope is deliberately narrow: only the master prompt of /sequence and
/video-sequence is checked, because those are the two commands that ship a
user's words off the appliance to a third-party LLM (Grok). Local ComfyUI
generation is not filtered.

Matching is case-insensitive and whole-word, where a run of non-alphanumeric
characters is a word boundary: a "damn" entry matches "damn!" and "(damn)" but
not "damned" — anchoring both ends is what keeps the classic Scunthorpe false
positive out. List the inflections you care about, or end an entry with "*" to
match any suffix ("fuck*" also catches "fucking" and "fucks"). An entry may
contain spaces, in which case it matches that phrase.
"""

import re

from config import PROFANITY_FILTER

# What counts as "inside a word" for the boundary lookarounds. Written out
# rather than using \b so that an entry ending in punctuation (e.g. "wtf?")
# still anchors sanely — \b's meaning flips depending on the last character.
_WORD_CHARS = "a-z0-9"


def parse_words(raw):
    """Split a PROFANITY_FILTER value into a de-duplicated list of entries."""
    words = []
    for part in (raw or "").split(","):
        word = part.strip().lower()
        if word and word not in words:
            words.append(word)
    return words


def _compile(words):
    """Compile `words` into one alternation, or None if there is nothing to match."""
    parts = []
    for word in words:
        wildcard = word.endswith("*")
        stem = word[:-1] if wildcard else word
        if not stem:
            continue
        # A wildcard entry swallows the rest of the word so the hit is reported
        # as the user typed it ("fucking", not "fuck").
        tail = f"[{_WORD_CHARS}]*" if wildcard else f"(?![{_WORD_CHARS}])"
        parts.append(f"(?<![{_WORD_CHARS}]){re.escape(stem)}{tail}")
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


WORDS = parse_words(PROFANITY_FILTER)
_PATTERN = _compile(WORDS)


def configure(raw):
    """(Re)load the word list from a raw PROFANITY_FILTER-style string.

    Production loads it once at import; this exists so tests (and anything that
    wants to reconfigure without a restart) don't have to reach into module
    globals. Returns the parsed word list.
    """
    global WORDS, _PATTERN
    WORDS = parse_words(raw)
    _PATTERN = _compile(WORDS)
    return WORDS


def enabled():
    """True when a non-empty word list is configured."""
    return _PATTERN is not None


def find_profanity(text):
    """Return the offending words found in `text`, in order, deduped.

    Reports the text as it appears in the prompt (so a "fuck*" entry reports the
    actual "fucking" the user typed), which makes the error message actionable.
    """
    if _PATTERN is None or not text:
        return []
    seen = set()
    hits = []
    for match in _PATTERN.finditer(text):
        hit = match.group(0)
        key = hit.lower()
        if key not in seen:
            seen.add(key)
            hits.append(hit)
    return hits


def check(text):
    """Return a user-facing error message if `text` trips the filter, else None."""
    hits = find_profanity(text)
    if not hits:
        return None
    quoted = ", ".join(f'"{h}"' for h in hits)
    return (f"Blocked by the profanity filter: {quoted}. "
            f"Reword the prompt and try again.")
