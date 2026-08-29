"""Per-image seed index — which seed produced which output file.

Backs the right-click "Copy seed" menu item, which pins *that specific image's*
seed for the next generation. This is deliberately distinct from
``generation_service._last_seed`` (the process-global "last seed" behind
``/getseed``): by the time an image worth reproducing is on screen, several more
have usually been generated over the top of it, so a single global is rarely the
one you are looking at. It also survives a restart, which ``_last_seed`` does not.

Storage is one flat JSON map, ``{filename: "seed-as-string"}``, at
``config.SEEDS_FILE`` (``.seeds.json`` under ``IMAGES_DIR``). Seeds are stored as
strings because they range up to ``2**64-1``, which a JS ``Number`` cannot hold
exactly — the same reason ``/api/last-seed`` returns a string.

**No in-memory cache, by design.** ``IMAGES_DIR`` is the lazily-mounted encrypted
output volume, so for the first seconds after login it is an empty stand-in
directory. A dict loaded then would cache "no seeds" for the life of the process —
exactly the failure ``@requires_output_storage`` exists to prevent. The file is
small and generations are seconds apart, so re-reading per call is cheap.
"""

import json
import threading

from config import IMAGES_DIR, SEEDS_FILE, SEED_STORE_MAX
from persistence import atomic_write_json

# Serialises the read-modify-write cycle below. One Gunicorn worker (see
# gunicorn.conf.py), but several generation threads can finish concurrently.
_lock = threading.Lock()


def _load():
    """Read the seed map, returning {} for a missing, empty or corrupt file."""
    try:
        data = json.loads(SEEDS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(store):
    """Write the seed map, best-effort. Losing a seed must never fail a generation."""
    try:
        atomic_write_json(SEEDS_FILE, store)
    except OSError:
        pass


def _prune(store):
    """Bound the store to SEED_STORE_MAX entries, in place.

    Drops entries whose image is gone first (archiving *moves* files out of
    IMAGES_DIR, so this self-heals without an archive hook), then oldest-first —
    dicts preserve insertion order, and entries are inserted in generation order.
    """
    if len(store) <= SEED_STORE_MAX:
        return
    for name in [n for n in store if not (IMAGES_DIR / n).is_file()]:
        del store[name]
    for name in list(store)[:max(0, len(store) - SEED_STORE_MAX)]:
        del store[name]


def record_seeds(filenames, seed):
    """Remember that ``seed`` produced each of ``filenames``. Best-effort.

    Called for every generation, not just the t2i/i2v/t2v ones that set
    ``_last_seed`` — the file exists either way, so the seed that made it may as
    well be recorded. A workflow with no seed input passes ``seed=None`` and is
    skipped.
    """
    if seed is None or not filenames:
        return
    with _lock:
        store = _load()
        for name in filenames:
            # Re-insert at the end so _prune's oldest-first drop stays accurate.
            store.pop(name, None)
            store[name] = str(seed)
        _prune(store)
        _save(store)


def get_seed(filename):
    """Return the recorded seed for ``filename`` as a string, or None."""
    with _lock:
        seed = _load().get(filename)
    return seed if isinstance(seed, str) else None


def forget(filename):
    """Drop one entry (the image was deleted). Best-effort."""
    with _lock:
        store = _load()
        if store.pop(filename, None) is not None:
            _save(store)


def clear():
    """Drop every entry (all images were deleted). Best-effort."""
    with _lock:
        _save({})
