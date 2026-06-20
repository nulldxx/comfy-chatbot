import re
import json
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename

from config import IMAGES_DIR, MEDIA_EXTS


def slugify(name):
    """Lower-case slug: collapse non-alphanumeric runs to hyphens.

    E.g. "Man walking on Beach" -> "man-walking-on-beach". Returns "" if
    nothing usable remains, so callers can fall back to a generated name.
    """
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

def sessions_dir():
    d = IMAGES_DIR / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_sessions():
    """Return a list of session summary dicts, sorted newest-first."""
    d = IMAGES_DIR / "sessions"
    if not d.is_dir():
        return []
    result = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            result.append({
                "name": f.stem,
                "saved_at": data.get("saved_at", ""),
                "image_count": len(data.get("sessionImages", [])),
            })
        except Exception:
            result.append({"name": f.stem, "saved_at": "", "image_count": 0})
    return result


def save_session(name, body):
    """Persist session data under sessions_dir/<name>.json. Returns the path."""
    path = sessions_dir() / f"{name}.json"
    payload = {k: v for k, v in body.items() if k != "name"}
    payload["saved_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_session(safe_name):
    """Load and filter a session, removing references to deleted images.

    Returns the session dict, or raises FileNotFoundError / OSError.
    """
    path = IMAGES_DIR / "sessions" / f"{safe_name}.json"
    if not path.is_file():
        raise FileNotFoundError(safe_name)
    data = json.loads(path.read_text())

    # Filter sessionImages and imagePrompts to files that still exist on disk.
    valid = set()
    for url in data.get("sessionImages", []):
        filename = url.rsplit("/", 1)[-1]
        safe_name_inner = secure_filename(filename)
        if (safe_name_inner
                and Path(safe_name_inner).suffix.lower() in MEDIA_EXTS
                and (IMAGES_DIR / safe_name_inner).is_file()):
            valid.add(url)

    data["sessionImages"] = [u for u in data.get("sessionImages", []) if u in valid]
    data["imagePrompts"] = {k: v for k, v in data.get("imagePrompts", {}).items() if k in valid}

    filtered = []
    for msg in data.get("messages", []):
        if msg.get("role") == "bot" and "images" in msg:
            msg["images"] = [u for u in msg["images"] if u in valid]
            if msg["images"] or msg.get("text"):
                filtered.append(msg)
        else:
            filtered.append(msg)
    data["messages"] = filtered

    return data


def delete_session(safe_name):
    """Delete a session file. Raises FileNotFoundError if it doesn't exist."""
    path = IMAGES_DIR / "sessions" / f"{safe_name}.json"
    if not path.is_file():
        raise FileNotFoundError(safe_name)
    path.unlink()


# ---------------------------------------------------------------------------
# Prompt aliases
# ---------------------------------------------------------------------------

def aliases_file():
    return IMAGES_DIR / "aliases.json"


def load_aliases():
    f = aliases_file()
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def save_aliases(aliases):
    aliases_file().write_text(json.dumps(aliases, indent=2))
