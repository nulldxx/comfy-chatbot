"""Liveness probes for the ComfyUI servers in the catalogue, and remote power
control of the ones running ComfyTray.

Two independent questions per server, because not every server has a tray:

* **Is ComfyUI up?** — asked of ComfyUI itself (``GET /system_stats``), so it is
  answerable for every server whether or not anything manages it. This is the one
  that decides whether a generation would work right now.
* **Is ComfyTray there, and what does it say?** — asked of the tray's REST API on
  the *same host* at ``COMFY_TRAY_PORT``. A tray that does not answer means an
  unmanaged server: a normal state, reported as ``reachable: False`` rather than
  an error, and the UI simply offers no start/stop for it.

The tray's contract (ComfyTray ``ApiRoutes.cs``) is three routes returning one
camelCase payload — ``{running, state, pid, port, uptimeSeconds, changed}`` —
with ``pid``/``port``/``uptimeSeconds`` null while stopped, and a ``500``
carrying ``{"error": ...}`` when a launch fails. Start and stop are idempotent:
asking for the state it is already in is a ``200`` with ``changed: false``, not
an error, so a caller never has to special-case it.

Imports nothing from ``app``, so it is unit-testable without a Flask context —
the same rule ``idle_lock.py`` and ``archive_browse.py`` follow.
"""

from concurrent.futures import ThreadPoolExecutor

import requests

from config import COMFY_TRAY_PORT, SERVER_PROBE_TIMEOUT, SERVER_POWER_TIMEOUT

# Cap on the fan-out in probe_all. Every probe is a short blocking socket read, so
# the threads cost nothing but a handful of descriptors; the cap only stops a very
# large servers.json from spawning a thread per entry.
MAX_PROBE_WORKERS = 16

TRAY_ACTIONS = ("start", "stop")


class TrayError(Exception):
    """ComfyTray answered, but refused — its /api/start returned a 500 because the
    launch failed. Carries the tray's own message, which names the real cause."""


def tray_address(host):
    """Where the tray API lives for a server: its host, the tray's own port."""
    return f"{host}:{COMFY_TRAY_PORT}"


def _short_error(exc):
    """A one-line reason for the UI. requests' exception strings embed the whole
    URL and the underlying urllib3 repr, which is unreadable in a chat bubble."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "timed out"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "timed out"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection refused"
    return type(exc).__name__


def probe_comfy(address, timeout=None):
    """Is ComfyUI itself listening at ``host:port``?

    ``/system_stats`` is ComfyUI's own cheap liveness endpoint and needs no
    session or client id. Any transport failure is an answer ("no"), never a
    raise — a down server is the normal case this panel exists to show.
    """
    timeout = SERVER_PROBE_TIMEOUT if timeout is None else timeout
    try:
        response = requests.get(f"http://{address}/system_stats", timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return {"reachable": False, "version": None, "error": _short_error(e)}
    except ValueError:
        # Something answered on the port but it is not ComfyUI. Reachable is still
        # the wrong word for it: a generation would fail.
        return {"reachable": False, "version": None, "error": "not ComfyUI"}

    system = data.get("system") if isinstance(data, dict) else None
    version = system.get("comfyui_version") if isinstance(system, dict) else None
    return {"reachable": True, "version": version, "error": None}


def _normalise_status(data):
    """ComfyTray's camelCase payload in this app's snake_case, with the nulls it
    sends while stopped preserved as None rather than coerced to 0."""
    return {
        "reachable": True,
        "running": bool(data.get("running")),
        "pid": data.get("pid"),
        "port": data.get("port"),
        "uptime_seconds": data.get("uptimeSeconds"),
        "changed": bool(data.get("changed")),
        "error": None,
    }


def _unreachable(reason):
    return {"reachable": False, "running": False, "pid": None, "port": None,
            "uptime_seconds": None, "changed": False, "error": reason}


def probe_tray(host, timeout=None):
    """Ask the tray on ``host`` what ComfyUI is doing.

    An unanswered probe is an unmanaged server, not a failure — the caller shows
    "no tray" and withholds the power buttons.
    """
    timeout = SERVER_PROBE_TIMEOUT if timeout is None else timeout
    try:
        response = requests.get(f"http://{tray_address(host)}/api/status",
                                timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return _unreachable(_short_error(e))
    except ValueError:
        return _unreachable("not ComfyTray")

    if not isinstance(data, dict):
        return _unreachable("not ComfyTray")
    return _normalise_status(data)


def tray_power(host, action, timeout=None):
    """Start or stop ComfyUI on ``host`` through its tray.

    Returns the tray's resulting status, whose ``changed`` says whether this call
    is what changed it — idempotent, so "it was already running" comes back as a
    success with ``changed: False``.

    Note the asynchronous start: the tray returns as soon as the process is
    spawned, so ``running`` goes true well before ComfyUI is actually listening.
    Callers must re-probe rather than trust this as the final state.

    Raises ``TrayError`` when the tray refuses or cannot be reached.
    """
    if action not in TRAY_ACTIONS:
        raise ValueError(f"unknown action: {action!r}")
    timeout = SERVER_POWER_TIMEOUT if timeout is None else timeout
    address = tray_address(host)
    try:
        response = requests.post(f"http://{address}/api/{action}", timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise TrayError(
            f"ComfyTray isn't answering on {address} ({_short_error(e)}) — "
            f"this server isn't managed by ComfyTray."
        ) from e

    if response.status_code != 200:
        # A failed launch is a 500 carrying the reason; anything else is a tray we
        # do not understand, and its status code is the most useful thing to say.
        try:
            message = (response.json() or {}).get("error")
        except ValueError:
            message = None
        raise TrayError(message or f"ComfyTray returned HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError as e:
        raise TrayError("ComfyTray sent an unreadable response") from e
    return _normalise_status(data)


def probe_one(entry, timeout=None):
    """Both probes for one catalogue entry, merged onto a copy of the entry."""
    host = entry.get("host", "")
    port = entry.get("port")
    address = f"{host}:{port}"
    return {
        "name": entry.get("name", address),
        "host": host,
        "port": port,
        "os": entry.get("os", "unix"),
        "address": address,
        "auto_purge": entry.get("auto_purge", True) is not False,
        "comfy": probe_comfy(address, timeout),
        "tray": probe_tray(host, timeout),
    }


def probe_all(entries, timeout=None):
    """Probe every server concurrently.

    Fanned out because the probes are entirely blocking waits: done serially, N
    servers with one dead host among them would cost N x the timeout, and the
    panel is meant to answer promptly.
    """
    entries = list(entries)
    if not entries:
        return []
    workers = min(MAX_PROBE_WORKERS, len(entries) * 2)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda e: probe_one(e, timeout), entries))
