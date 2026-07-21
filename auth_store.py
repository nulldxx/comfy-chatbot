"""Secure, persistent storage for the user-changeable login password.

The login password can be changed from the UI (see /api/change-password in app.py
and the /change-password slash command). The new value is stored as a salted,
memory-hard **scrypt** hash in a JSON file on the non-encrypted, redeploy-surviving
workflows mount (COMFY_WORKFLOW_DIR) -- deliberately NOT on the LUKS-encrypted
IMAGES_DIR, and never as plaintext.

Precedence: once a password has been set via the UI (the hash file exists), it is
authoritative and the env APP_PASSWORD no longer works. Until then, login bootstraps
against APP_PASSWORD (config.PASSWORD). Reset = delete AUTH_FILE on the host to revert
to the env password.

scrypt is implemented directly against hashlib (available since Python 3.6 with an
OpenSSL that supports it) rather than werkzeug.security, because the pinned
Werkzeug 2.3.7 predates its scrypt support and we don't want to force a Flask/Werkzeug
major bump for the whole app. check_password uses a constant-time comparison.
"""

import os
import json
import hmac
import hashlib
import secrets
import threading
from datetime import datetime

from config import COMFY_WORKFLOW_DIR, PASSWORD

# scrypt work factors. Memory cost ~= 128 * N * r = 16 MiB per hash at these values
# -- fine for occasional interactive logins, painful for offline brute force.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024  # headroom above the ~16 MiB the params require
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

AUTH_FILE = COMFY_WORKFLOW_DIR / ".auth.json"

# Serialises writes to AUTH_FILE. One Gunicorn worker (see gunicorn.conf.py), so a
# single lock suffices, mirroring persistence.sessions_write_lock.
_auth_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# In-memory login password (for deriving the LUKS passphrase — see crypto_key).
# ---------------------------------------------------------------------------
# The plaintext login password is held in memory for the process lifetime after a
# successful login and is NEVER persisted. It is the second input (with SECRET_KEY)
# to the derived LUKS passphrase, so once a UI password is set the encrypted volumes
# can only be unlocked while someone has logged in since the process started. On
# restart it is gone until the next login — that is the whole point (see the
# archive-rekeying plan / lazy output mount). One Gunicorn worker with shared
# threads, so a module-global guarded by a lock is process-wide.
_session_password = None
_session_password_lock = threading.Lock()


def set_session_password(plaintext):
    """Record the plaintext login password in memory (called on successful login
    and after a password change). Never written to disk."""
    global _session_password
    with _session_password_lock:
        _session_password = plaintext


def current_password():
    """Return the in-memory login password, or None if nobody has logged in since
    this process started."""
    with _session_password_lock:
        return _session_password


def clear_session_password():
    """Forget the in-memory login password (relocks the volumes on next open)."""
    global _session_password
    with _session_password_lock:
        _session_password = None


def generate_recovery_passphrase():
    """Return a fresh high-entropy recovery passphrase for a spare LUKS keyslot.

    Shown to the user once on the first password set and never stored server-side;
    it is the only way to recover the archive if the login password is forgotten.
    URL-safe base64 of 32 random bytes (~256 bits)."""
    return secrets.token_urlsafe(32)


def _hash_password(plaintext, salt):
    """Return the scrypt digest (bytes) of ``plaintext`` with ``salt`` (bytes)."""
    return hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM, dklen=_SCRYPT_DKLEN,
    )


def _encode(salt, digest):
    """Encode salt+digest into a single self-describing string."""
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, salt.hex(), digest.hex()
    )


def _verify_encoded(encoded, plaintext):
    """Constant-time check of ``plaintext`` against a stored ``encoded`` hash."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            plaintext.encode("utf-8"),
            salt=salt,
            n=int(n), r=int(r), p=int(p),
            maxmem=_SCRYPT_MAXMEM, dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def load_password_hash():
    """Return the stored encoded hash string, or None if no password has been set."""
    try:
        data = json.loads(AUTH_FILE.read_text())
    except (OSError, ValueError):
        return None
    h = data.get("password_hash")
    return h if isinstance(h, str) and h else None


def password_is_set():
    """True once a password has been set via the UI (the hash file exists)."""
    return load_password_hash() is not None


def save_password_hash(plaintext):
    """Persist a scrypt hash of ``plaintext`` atomically, with 0600 perms."""
    salt = secrets.token_bytes(_SALT_BYTES)
    encoded = _encode(salt, _hash_password(plaintext, salt))
    payload = {"password_hash": encoded, "updated_at": datetime.now().isoformat()}
    tmp = AUTH_FILE.with_suffix(AUTH_FILE.suffix + ".tmp")
    with _auth_write_lock:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, AUTH_FILE)


def verify_password(plaintext):
    """True if ``plaintext`` is the current login password.

    Uses the stored hash when a password has been set; otherwise bootstraps against
    the env APP_PASSWORD (config.PASSWORD). Both comparisons are constant-time.
    """
    if plaintext is None:
        plaintext = ""
    stored = load_password_hash()
    if stored is not None:
        return _verify_encoded(stored, plaintext)
    return hmac.compare_digest(plaintext, PASSWORD or "")
