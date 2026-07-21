#!/usr/bin/env python3
"""agent_client — talk to the host archive-agent over its Unix socket.

Used both by app.py (the /archive-* mount/unmount flow) and by the container
entrypoint (create + mount the encrypted output volume on start, unmount it on
stop). Stdlib only, so it runs unchanged in the slim runtime image.

CLI:
  python -m agent_client mount-output     # create-if-missing + mount, wait for marker
  python -m agent_client unmount-output   # unmount (lock) the output volume
  python -m agent_client check-output     # e2fsck the output volume (before mount)

All CLI commands are no-ops (exit 0) when OUTPUT_VOLUME is unset, so the
entrypoint can call them unconditionally. check-output is best-effort: e2fsck can
only run on an unmounted filesystem, so it must come BEFORE mount-output, and it
never blocks startup (its result is recorded to OUTPUT_FSCHECK_RESULT for
/api/fscheck to surface).
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


def _password_deferred():
    """True once a UI login password has been set. When it is, the encrypted volumes
    are keyed on a password-derived passphrase (see crypto_key) that isn't available
    at startup, so the entrypoint must NOT auto-mount/-check the output volume — it is
    deferred to the app's first-login flow (app._lazy_output_check_and_mount). The
    login-hash file lives on the unencrypted workflows mount, readable at startup."""
    try:
        import auth_store
        return auth_store.password_is_set()
    except Exception:
        # If we can't tell (e.g. workflows mount not ready), behave as today rather
        # than silently skipping the mount and starting with no output storage.
        return False


def _mount_output():
    """Ask the agent to create-if-missing + mount the output volume, then block
    until the mount marker propagates into COMFY_OUTPUT_DIR. Returns a process
    exit code: 0 = mounted (or encryption disabled), non-zero = refuse to start."""
    volume = _env("OUTPUT_VOLUME")
    if not volume:
        return 0  # output encryption disabled — nothing to do
    if _password_deferred():
        print("output-volume: a login password is set — output mount deferred to "
              "first login", file=sys.stderr)
        return 0
    # Passphrase: explicit OUTPUT_PASSWORD, else reuse SECRET_KEY so a deployment
    # only has to declare one secret. SECRET_KEY is always set, so this never
    # leaves the volume passphrase-less.
    password = _env("OUTPUT_PASSWORD") or _env("SECRET_KEY")
    if not password:
        print("output-volume: OUTPUT_VOLUME is set but no OUTPUT_PASSWORD/"
              "SECRET_KEY configured", file=sys.stderr)
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


def _write_fscheck_result(path, resp):
    """Record the output volume's fsck result (with a timestamp) so the Flask app
    can surface it via /api/fscheck. Best-effort — a write failure is only logged."""
    record = {"checked_at": time.time()}
    record.update(resp)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
    except OSError as exc:
        print(f"output-fsck: could not write result to {path}: {exc}", file=sys.stderr)


def _check_output():
    """Ask the agent to e2fsck the (unmounted) output volume and record the result.

    Best-effort by design: it runs before mount-output, and a dirty/uncorrectable
    filesystem or an unreachable agent must NOT stop the container from starting —
    the outcome is logged and written to OUTPUT_FSCHECK_RESULT for later display.
    Always returns 0."""
    volume = _env("OUTPUT_VOLUME")
    if not volume:
        return 0  # output encryption disabled — nothing to check
    if _password_deferred():
        print("output-fsck: a login password is set — output check deferred to "
              "first login", file=sys.stderr)
        return 0
    password = _env("OUTPUT_PASSWORD") or _env("SECRET_KEY")
    if not password:
        print("output-fsck: OUTPUT_VOLUME set but no OUTPUT_PASSWORD/SECRET_KEY; "
              "skipping check", file=sys.stderr)
        return 0
    socket_path = _env("ARCHIVE_AGENT_SOCKET", "/run/archive-agent.sock")
    # Wait longer than the agent's e2fsck ceiling so we get its result rather than
    # timing out mid-check (keep the default in sync with config.FSCK_TIMEOUT).
    timeout = float(_env("FSCK_TIMEOUT", "1200"))
    result_path = _env("OUTPUT_FSCHECK_RESULT", "/tmp/comfy-output-fscheck.json")

    # The output mount lives in the host mount namespace (the agent runs with
    # MountFlags=shared), so it survives container restarts. An unclean stop — or a
    # deployment predating the shutdown unmount — leaves it mounted, and e2fsck
    # refuses a mounted fs. Unmount first (best-effort): it's safe here because the
    # app isn't serving yet and mount-output remounts it on the next line of the
    # entrypoint. A "not mounted" error is expected on a clean boot and ignored.
    try:
        send({"action": "unmount", "target": "output", "volume": volume}, socket_path)
    except RuntimeError as exc:
        print(f"output-fsck: pre-check unmount skipped: {exc}", file=sys.stderr)

    try:
        resp = send({
            "action": "fsck",
            "target": "output",
            "volume": volume,
            "password": password,
        }, socket_path, timeout=timeout)
    except RuntimeError as exc:
        print(f"output-fsck: {exc}", file=sys.stderr)
        resp = {"ok": False, "error": str(exc)}

    _write_fscheck_result(result_path, resp)

    if resp.get("skipped"):
        print("output-fsck: volume not yet provisioned; skipped", file=sys.stderr)
    elif resp.get("clean"):
        print("output-fsck: filesystem clean", file=sys.stderr)
    elif resp.get("uncorrected"):
        print("output-fsck: WARNING — errors could not be fully corrected",
              file=sys.stderr)
    elif resp.get("corrected"):
        print("output-fsck: filesystem errors corrected", file=sys.stderr)
    elif not resp.get("ok"):
        print(f"output-fsck: check failed: {resp.get('error')}", file=sys.stderr)
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "mount-output":
        return _mount_output()
    if cmd == "unmount-output":
        return _unmount_output()
    if cmd == "check-output":
        return _check_output()
    print(f"usage: {argv[0]} mount-output|unmount-output|check-output",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
