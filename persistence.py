import os
import re
import json
import threading
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename

from config import IMAGES_DIR, MEDIA_EXTS

# Serialises every mutation of a session JSON file. Two writers race on the same
# sessions/<name>.json: the client's full-doc overwrite (save_session, via
# /api/sessions) and the server-side sequence run's incremental append
# (append_session_image). All session-file writes take this lock and write
# atomically (temp file + os.replace) so a reader never sees a half-written doc
# and concurrent writers can't lose each other's updates. A single threading.Lock
# suffices because the app runs one Gunicorn worker (see gunicorn.conf.py).
sessions_write_lock = threading.Lock()


def _atomic_write_json(path, data):
    """Write ``data`` as pretty JSON to ``path`` atomically (temp file + replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


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
    with sessions_write_lock:
        _atomic_write_json(path, payload)
    return path


def append_session_image(name, url, prompt, video_meta=None, settings=None):
    """Append one completed image to a session file, creating it if needed.

    Used by the server-side sequence run (generation_service.run_sequence_run) so
    that images generated after the browser has disconnected are still persisted
    and can be recovered later via /session-load. Mirrors the doc shape produced
    client-side (doRecordSave / captureSessionMessages) so restoreSession can
    rebuild the chat: a user message carrying the prompt followed by a bot message
    carrying the image. Runs under sessions_write_lock as a read-modify-write and
    writes atomically. Returns the updated document.
    """
    path = sessions_dir() / f"{name}.json"
    with sessions_write_lock:
        if path.is_file():
            try:
                doc = json.loads(path.read_text())
            except Exception:
                doc = {}
        else:
            doc = {}

        doc.setdefault("sessionImages", [])
        doc.setdefault("imagePrompts", {})
        doc.setdefault("imageVideoMeta", {})
        doc.setdefault("messages", [])
        doc["recordingName"] = name
        # Only seed settings the first time, so a later client overwrite (or a
        # rename) doesn't get its richer settings clobbered by our subset.
        if settings and not doc.get("settings"):
            doc["settings"] = settings

        if url not in doc["sessionImages"]:
            doc["sessionImages"].append(url)
        doc["imagePrompts"][url] = prompt
        if video_meta is not None:
            doc["imageVideoMeta"][url] = video_meta

        doc["messages"].append({"role": "user", "prompt": prompt})
        doc["messages"].append({"role": "bot", "images": [url], "text": ""})

        doc["saved_at"] = datetime.now().isoformat()
        _atomic_write_json(path, doc)
    return doc


def rename_session(src, dst):
    """Move sessions/<src>.json to sessions/<dst>.json, rewriting recordingName.

    Raises FileNotFoundError if src is missing, FileExistsError if dst already
    exists. Runs under sessions_write_lock so it can't interleave with an
    in-flight append. Returns the destination name.
    """
    d = sessions_dir()
    src_path = d / f"{src}.json"
    dst_path = d / f"{dst}.json"
    with sessions_write_lock:
        if not src_path.is_file():
            raise FileNotFoundError(src)
        if dst_path.exists():
            raise FileExistsError(dst)
        try:
            doc = json.loads(src_path.read_text())
        except Exception:
            doc = {}
        doc["recordingName"] = dst
        _atomic_write_json(dst_path, doc)
        src_path.unlink()
    return dst


def load_session(safe_name):
    """Load and filter a session, removing references to deleted images.

    Returns the session dict, or raises FileNotFoundError / OSError.
    """
    path = IMAGES_DIR / "sessions" / f"{safe_name}.json"
    if not path.is_file():
        raise FileNotFoundError(safe_name)
    data = json.loads(path.read_text())

    # Filter sessionImages, imagePrompts and imageVideoMeta to files that still
    # exist on disk.
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
    data["imageVideoMeta"] = {k: v for k, v in data.get("imageVideoMeta", {}).items() if k in valid}

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


# ---------------------------------------------------------------------------
# Macros
# ---------------------------------------------------------------------------

def macros_file():
    return IMAGES_DIR / "macros.json"


def load_macros():
    f = macros_file()
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def save_macros(macros):
    macros_file().write_text(json.dumps(macros, indent=2))
