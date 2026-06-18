#!/usr/bin/env python3
"""agent_client — talk to the host archive-agent over its Unix socket.

Used both by app.py (the /archive-* mount/unmount flow) and by the container
entrypoint (create + mount the encrypted output volume on start, unmount it on
stop). Stdlib only, so it runs unchanged in the slim runtime image.

CLI:
  python -m agent_client mount-output     # create-if-missing + mount, wait for marker
  python -m agent_client unmount-output   # unmount (lock) the output volume

Both CLI commands are no-ops (exit 0) when OUTPUT_VOLUME is unset, so the
entrypoint can call them unconditionally.
"""

import os
import sys
import json
import time
import socket

# Marker file the agent drops at the volume root on mount. Kept in sync with
# MARKER_NAME in packaging/agent/archive-agent and ARCHIVE_MARKER in app.py.
MARKER_NAME = ".comfy-archive"


def send(payload, socket_path, timeout=120.0):
    """Send one newline-delimited JSON request to the agent and return the parsed
    reply. Raises RuntimeError on transport failure or a malformed reply."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
    except OSError as exc:
        raise RuntimeError(f"archive agent unavailable: {exc}") from exc
    raw = b"".join(chunks).decode("utf-8").strip()
    if not raw:
        raise RuntimeError("archive agent returned no response")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"archive agent returned invalid response: {raw!r}") from exc


def _env(name, default=""):
    return os.environ.get(name, default)


def _mount_output():
    """Ask the agent to create-if-missing + mount the output volume, then block
    until the mount marker propagates into COMFY_OUTPUT_DIR. Returns a process
    exit code: 0 = mounted (or encryption disabled), non-zero = refuse to start."""
    volume = _env("OUTPUT_VOLUME")
    if not volume:
        return 0  # output encryption disabled — nothing to do
    password = _env("OUTPUT_PASSWORD") or _env("ARCHIVE_PASSWORD")
    if not password:
        print("output-volume: OUTPUT_VOLUME is set but no OUTPUT_PASSWORD/"
              "ARCHIVE_PASSWORD configured", file=sys.stderr)
        return 1
    socket_path = _env("ARCHIVE_AGENT_SOCKET", "/run/archive-agent.sock")
    images_dir = _env("COMFY_OUTPUT_DIR", "/app/output")
    marker = os.path.join(images_dir, MARKER_NAME)

    try:
        resp = send({
            "action": "mount",
            "target": "output",
            "volume": volume,
            "password": password,
            "create": True,
            "size": _env("OUTPUT_SIZE", "20G"),
        }, socket_path)
    except RuntimeError as exc:
        print(f"output-volume: {exc}", file=sys.stderr)
        return 1
    if not resp.get("ok"):
        print(f"output-volume: mount failed: {resp.get('error')}", file=sys.stderr)
        return 1

    # The mount happens on the host and propagates in via the rshared bind; wait
    # for the marker so we never start serving onto plain disk.
    for _ in range(50):
        if os.path.exists(marker):
            print("output-volume: encrypted output mounted", file=sys.stderr)
            return 0
        time.sleep(0.1)
    print(f"output-volume: mounted but marker never appeared at {marker}; "
          "refusing to start", file=sys.stderr)
    return 1


def _unmount_output():
    """Ask the agent to unmount (lock) the output volume. Best-effort: failures
    are logged but never block container shutdown."""
    volume = _env("OUTPUT_VOLUME")
    if not volume:
        return 0
    socket_path = _env("ARCHIVE_AGENT_SOCKET", "/run/archive-agent.sock")
    try:
        resp = send({"action": "unmount", "target": "output", "volume": volume},
                    socket_path)
    except RuntimeError as exc:
        print(f"output-volume: unmount error: {exc}", file=sys.stderr)
        return 1
    if not resp.get("ok"):
        print(f"output-volume: unmount failed: {resp.get('error')}", file=sys.stderr)
        return 1
    print("output-volume: encrypted output unmounted", file=sys.stderr)
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "mount-output":
        return _mount_output()
    if cmd == "unmount-output":
        return _unmount_output()
    print(f"usage: {argv[0]} mount-output|unmount-output", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
