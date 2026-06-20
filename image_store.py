import secrets
import uuid
import threading
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import jsonify

from config import (
    IMAGES_DIR, MASKS_DIR, INPAINT_INPUTS_DIR,
    IMAGE_EXTS, OUTPUT_VOLUME, OUTPUT_MARKER,
)

# Mask token registry: opaque token → (session_user, Path). Single-use; consumed atomically on /api/inpaint.
mask_tokens: dict = {}
mask_tokens_lock = threading.Lock()

# Drawn-input token registry: opaque token → (session_user, Path). Same single-use
# lifecycle as mask_tokens; consumed atomically on /api/inpaint when the user drew
# a hint onto the image.
draw_tokens: dict = {}
draw_tokens_lock = threading.Lock()

MAX_MASK_BYTES = 10 * 1024 * 1024  # 10 MB decoded


def register_mask_token(user, image_bytes):
    """Save mask bytes to MASKS_DIR and return an opaque single-use token."""
    mask_path = MASKS_DIR / f"mask_{uuid.uuid4().hex}.png"
    mask_path.write_bytes(image_bytes)
    token = secrets.token_urlsafe(32)
    with mask_tokens_lock:
        mask_tokens[token] = (user, mask_path)
    return token


def register_draw_token(user, image_bytes):
    """Save drawn-input bytes to INPAINT_INPUTS_DIR and return an opaque single-use token."""
    draw_path = INPAINT_INPUTS_DIR / f"draw_{uuid.uuid4().hex}.png"
    draw_path.write_bytes(image_bytes)
    token = secrets.token_urlsafe(32)
    with draw_tokens_lock:
        draw_tokens[token] = (user, draw_path)
    return token


def resolve_mask(token, user):
    """Atomically consume a mask token. Return (path, None) or (None, error_response).

    Pops the token from the registry under lock so concurrent calls with the
    same token cannot both succeed (single-use enforcement). Also validates that
    the token belongs to the requesting session user.
    """
    with mask_tokens_lock:
        entry = mask_tokens.pop(token, None)
    if entry is None:
        return None, (jsonify({"error": "Mask not found"}), 404)
    owner, mask_path = entry
    if owner != user:
        return None, (jsonify({"error": "Mask not found"}), 404)
    if not mask_path.is_file():
        return None, (jsonify({"error": "Mask not found"}), 404)
    return mask_path, None


def resolve_draw_image(token, user):
    """Atomically consume a drawn-input token. Return (path, None) or (None, error_response).

    Mirrors resolve_mask: pops under lock for single-use enforcement and validates
    that the token belongs to the requesting session user.
    """
    with draw_tokens_lock:
        entry = draw_tokens.pop(token, None)
    if entry is None:
        return None, (jsonify({"error": "Drawn image not found"}), 404)
    owner, draw_path = entry
    if owner != user:
        return None, (jsonify({"error": "Drawn image not found"}), 404)
    if not draw_path.is_file():
        return None, (jsonify({"error": "Drawn image not found"}), 404)
    return draw_path, None


def resolve_input_image(image_url):
    """Return (safe_name, path, None) on success or (None, None, error_response) on failure."""
    filename = image_url.rsplit("/", 1)[-1]
    safe = secure_filename(filename)
    if not safe or safe != filename:
        return None, None, (jsonify({"error": "Invalid image filename"}), 400)
    if Path(safe).suffix.lower() not in IMAGE_EXTS:
        return None, None, (jsonify({"error": "Source image must be a supported image type"}), 400)
    image_path = IMAGES_DIR / safe
    if not image_path.is_file():
        return None, None, (jsonify({"error": "Source image not found"}), 404)
    return safe, image_path, None


def select_images(scope, filenames=None):
    """Resolve an archive/listing scope to a list of image Paths in IMAGES_DIR.

    - "all"     -> every image file.
    - "today"   -> images whose mtime is today (mirrors the /api/images filter).
    - "session" -> the supplied filenames, validated like api_delete_image.
    Raises ValueError on an unknown scope or an invalid session filename.
    """
    if not IMAGES_DIR.is_dir():
        return []
    if scope == "session":
        selected = []
        for name in (filenames or []):
            safe = secure_filename(name)
            if not safe or safe != name:
                raise ValueError(f"Invalid filename: {name}")
            path = IMAGES_DIR / safe
            if path.suffix.lower() not in IMAGE_EXTS:
                raise ValueError(f"Invalid filename: {name}")
            if path.is_file():
                selected.append(path)
        return selected
    files = [p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in IMAGE_EXTS]
    if scope == "today":
        today = datetime.now().date()
        files = [
            p for p in files
            if datetime.fromtimestamp(p.stat().st_mtime).date() == today
        ]
    elif scope != "all":
        raise ValueError(f"Unknown scope: {scope}")
    return files


def output_storage_error():
    """If live-output encryption is enabled (OUTPUT_VOLUME set) but the encrypted
    volume isn't mounted here, return a Flask (response, status) tuple so callers
    refuse to start a generation that would write images to plain disk. Returns
    None when storage is healthy or encryption is disabled."""
    if OUTPUT_VOLUME and not (IMAGES_DIR / OUTPUT_MARKER).exists():
        return jsonify({"error": "Encrypted output volume is not mounted; "
                                 "refusing to write images to plain disk."}), 503
    return None
