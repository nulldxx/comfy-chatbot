"""crypto_key — derive the LUKS passphrase for the encrypted volumes.

Historically both encrypted volumes (the archive and the live-output volume) were
unlocked with SECRET_KEY, which sits in plaintext in the deployment's compose file.
Anyone who reads that file therefore holds the passphrase and can decrypt the volumes
offline.

This module makes the effective passphrase depend on the user-changeable **login
password** once one has been set (see auth_store). Because the password is only ever
stored as an unrecoverable scrypt hash, an attacker who has the compose file *and* the
hash file still cannot derive the volume key.

Stdlib only, so it imports cleanly in the slim runtime image, the container entrypoint
and the (stdlib-only) agent_client.

The derivation is **pinned**: once a volume has been re-keyed to a derived passphrase,
changing this function would orphan the keyslot and lose access to the data. Never
change ``derive_passphrase`` without re-keying every volume.
"""

import hashlib

# Separator between the two inputs so ("ab", "c") and ("a", "bc") can never collide.
_SEP = b"\x00"


class VolumeLockedError(RuntimeError):
    """A volume op needs the in-memory login password but none is available.

    Raised when a password has been set (so SECRET_KEY alone no longer unlocks the
    volumes) but nobody has logged in since the process started, so the plaintext
    password isn't held in memory yet. Callers should surface this as "log in to
    unlock" rather than falling back to SECRET_KEY (which would no longer work)."""


def derive_passphrase(secret_key, password):
    """Return the derived LUKS passphrase for ``secret_key`` + ``password``.

    ``sha256(secret_key || 0x00 || password)`` as hex — a fixed-width value with no
    length/ambiguity edge cases. **Pinned** — see the module docstring."""
    return hashlib.sha256(
        (secret_key or "").encode("utf-8") + _SEP + (password or "").encode("utf-8")
    ).hexdigest()


def effective_passphrase(secret_key, password_is_set, password):
    """Return the passphrase to open a volume, for the given state.

    - ``password_is_set`` False → ``secret_key`` (bootstrap; exactly the historic
      behaviour before any UI password was set).
    - ``password_is_set`` True with a ``password`` → the derived passphrase.
    - ``password_is_set`` True but no ``password`` in memory → ``VolumeLockedError``.
    """
    if not password_is_set:
        return secret_key
    if not password:
        raise VolumeLockedError(
            "encrypted volumes are locked; log in to unlock them"
        )
    return derive_passphrase(secret_key, password)
