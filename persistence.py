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


def atomic_write_json(path, data):
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
        atomic_write_json(path, payload)
    return path


def append_session_image(name, url, prompt, video_meta=None, settings=None, message_prompt=None):
    """Append one completed image to a session file, creating it if needed.

    Used by the server-side sequence run (generation_service.run_sequence_run) so
    that images generated after the browser has disconnected are still persisted
    and can be recovered later via /session-load. Mirrors the doc shape produced
    client-side (doRecordSave / captureSessionMessages) so restoreSession can
    rebuild the chat: a user message carrying the prompt followed by a bot message
    carrying the image. Runs under sessions_write_lock as a read-modify-write and
    writes atomically. Returns the updated document.

    ``message_prompt``, when given, is the user line's text in place of ``prompt``.
    An auto image2video stores the still's prompt against the video (as the client's
    i2v does) but shows the video prompt it was actually run with.
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

        doc["messages"].append({"role": "user", "prompt": message_prompt or prompt})
        doc["messages"].append({"role": "bot", "images": [url], "text": ""})

        doc["saved_at"] = datetime.now().isoformat()
        atomic_write_json(path, doc)
    return doc


def append_session_note(name, prompt, note):
    """Append a text-only note (no image) to a session, creating it if needed.

    Used when a sequence-run shot fails: there's no image to append via
    append_session_image, but the failure should still be visible on
    /session-load rather than silently vanishing as a gap in the sequence.
    Mirrors append_session_image's message shape but with an empty images list —
    load_session keeps a bot message with no images as long as it has text, so
    this survives the same filtering that would otherwise drop an empty message.
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

        doc["messages"].append({"role": "user", "prompt": prompt})
        doc["messages"].append({"role": "bot", "images": [], "text": note})

        doc["saved_at"] = datetime.now().isoformat()
        atomic_write_json(path, doc)
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
        atomic_write_json(dst_path, doc)
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


def _media_filename(url):
    """Bare, validated gallery filename for a ``/images/<name>`` URL, else None.

    Same validation load_session applies before deciding an image still exists:
    the last path segment must survive secure_filename unchanged and carry a
    media extension. Anything else (a traversal attempt, a /references-file/ URL,
    a stray non-media name) is not ours to act on.
    """
    filename = (url or "").rsplit("/", 1)[-1]
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return None
    if Path(safe).suffix.lower() not in MEDIA_EXTS:
        return None
    return safe


def session_media_filenames(doc):
    """Every gallery media filename a session document references.

    Reads ``sessionImages`` plus each bot message's ``images``: the two normally
    agree, but a doc written by an older client (or half-updated by a crash) can
    carry an image in one and not the other, and both are equally "this chat's
    media".

    Deliberately does NOT include ``settings.references.images`` — a reference is
    a file the chat *points at*, typically generated by some other chat, so a
    chat's own media is not defined by it. _session_media_in_use reads references
    for the opposite reason: there they are evidence a file is still wanted.
    """
    names = set()
    for url in (doc.get("sessionImages") or []):
        name = _media_filename(url)
        if name:
            names.add(name)
    for msg in (doc.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        for url in (msg.get("images") or []):
            name = _media_filename(url)
            if name:
                names.add(name)
    return names


def _session_media_in_use(exclude):
    """Media filenames every *other* session still references.

    One gallery file can belong to several chats — the /review grid's "import into
    this session", /jobs' asset pull, a tab reattached to a run recording into a
    different chat, and a backup restored under a new name all duplicate a URL
    with nothing reference-counting it. Deleting a chat must therefore not delete
    a file another chat still lists.

    Also counts ``settings.references.images``: a URL pinned as a reference is a
    live use even though it is not in that chat's sessionImages, and unlike
    sessionImages it is never filtered by load_session, so a dangling one would
    not self-heal.

    Unreadable session files are skipped rather than fatal, but they are skipped
    on the *conservative* side only in the sense that the rest of the scan still
    runs; a corrupt neighbour cannot protect its files.
    """
    d = IMAGES_DIR / "sessions"
    if not d.is_dir():
        return set()
    in_use = set()
    for f in d.glob("*.json"):
        if f.stem == exclude:
            continue
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        in_use |= session_media_filenames(doc)
        settings = doc.get("settings") or {}
        refs = list((settings.get("references") or {}).get("images") or [])
        # Sessions saved before /references replaced the single reference slot
        # pin theirs here instead (restoreSession still reads it).
        if settings.get("refImageUrl"):
            refs.append(settings["refImageUrl"])
        for url in refs:
            name = _media_filename(url)
            if name:
                in_use.add(name)
    return in_use


def delete_session(safe_name, delete_media=False):
    """Delete a session file, optionally with the media it generated.

    Raises FileNotFoundError if the session doesn't exist. Returns
    ``{"deleted_media": [names], "kept_shared": n}`` — both empty when
    ``delete_media`` is false.

    Media that another session still references is kept (see
    _session_media_in_use). Media that is simply gone is a silent no-op, which is
    what makes archiving free here: /api/archive *moves* files off to the archive
    volume and unlinks the gallery original, so an archived image is already
    absent and needs no separate check.

    The session JSON is unlinked *first*: deleting the chat is the operation the
    user asked for and must not be lost to a media unlink that fails.

    Callers are responsible for the seed_store entries of the returned names —
    seed_store imports this module, so it cannot be imported from here.
    """
    path = IMAGES_DIR / "sessions" / f"{safe_name}.json"
    if not path.is_file():
        raise FileNotFoundError(safe_name)

    targets, shared = [], 0
    if delete_media:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            doc = {}
        if isinstance(doc, dict):
            names = session_media_filenames(doc)
            in_use = _session_media_in_use(safe_name)
            targets = sorted(names - in_use)
            shared = len(names & in_use)

    path.unlink()

    deleted = []
    for name in targets:
        try:
            (IMAGES_DIR / name).unlink(missing_ok=True)
            deleted.append(name)
        except OSError:
            continue
    return {"deleted_media": deleted, "kept_shared": shared}


def session_delete_preview(safe_name):
    """Count the media ``delete_session(delete_media=True)`` would remove.

    Backs the sidebar's confirm strip, which must show a true number: the
    sidebar's list already carries ``image_count``, but that is
    ``len(sessionImages)`` with no disk check, so it overstates as soon as
    anything has been archived or individually deleted. Only files that still
    exist and are not referenced elsewhere are counted.
    """
    path = IMAGES_DIR / "sessions" / f"{safe_name}.json"
    if not path.is_file():
        raise FileNotFoundError(safe_name)
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    names = session_media_filenames(doc)
    in_use = _session_media_in_use(safe_name)
    media = [n for n in (names - in_use) if (IMAGES_DIR / n).is_file()]
    return {"media": len(media), "shared": len(names & in_use)}


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


# ---------------------------------------------------------------------------
# Default macro (target of the 🤖 button on images)
# ---------------------------------------------------------------------------

def default_macro_file():
    return IMAGES_DIR / "default-macro.json"


def load_default_macro():
    """Return the persisted default macro name, or None if unset."""
    f = default_macro_file()
    if not f.is_file():
        return None
    try:
        name = json.loads(f.read_text()).get("name")
        return name if isinstance(name, str) and name else None
    except Exception:
        return None


def save_default_macro(name):
    """Persist the default macro name; a falsy name clears it (removes the file)."""
    f = default_macro_file()
    if not name:
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass
        return
    f.write_text(json.dumps({"name": name}, indent=2))
