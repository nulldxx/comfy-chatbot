#!/bin/sh
# Container entrypoint.
#
# When OUTPUT_VOLUME is set, ask the host archive-agent to create-if-missing +
# mount the encrypted output volume at COMFY_OUTPUT_DIR before starting the app,
# and to unmount it on shutdown — so generated images are encrypted at rest
# whenever the container isn't running. With OUTPUT_VOLUME unset both calls are
# no-ops and this just runs the app ("$@", normally gunicorn).
set -e

term() {
    # Forward shutdown to the app for a graceful stop, then lock the volume.
    if [ -n "$APP_PID" ]; then
        kill -TERM "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    python -m agent_client unmount-output || true
    exit 0
}

# Check + auto-repair the output volume's filesystem while it is still unmounted
# (e2fsck can't run on a mounted fs). Best-effort: never blocks startup — the
# result is recorded for /fscheck to surface. Must run BEFORE mount-output.
python -m agent_client check-output || true

# Mount (and on first deploy, create) the encrypted output volume before serving.
# A non-zero exit here means encryption is enabled but the volume didn't mount,
# so we abort rather than write plaintext images to disk.
python -m agent_client mount-output

trap term TERM INT

"$@" &
APP_PID=$!
wait "$APP_PID"
