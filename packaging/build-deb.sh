#!/bin/bash
# Build the comfy-archive-agent .deb package.
#
# Usage: VERSION=1.2.3 packaging/build-deb.sh [output-dir]
#   VERSION   package version (defaults to 0.0.0-dev). A leading "v" is stripped.
#   output-dir  where the .deb is written (defaults to ./dist).
#
# The package is Architecture: all (pure Python stdlib), so this builds on any
# host and installs on arm64 (Raspberry Pi 5 / Debian Bookworm) and amd64 alike.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

VERSION="${VERSION:-0.0.0-dev}"
VERSION="${VERSION#v}"
OUT_DIR="${1:-$REPO/dist}"

PKG="comfy-archive-agent"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# --- Lay out the install tree -------------------------------------------------
install -D -m 0755 "$HERE/agent/comfy-archive-agent" \
    "$STAGE/usr/bin/comfy-archive-agent"
install -D -m 0644 "$HERE/agent/comfy-archive-agent.service" \
    "$STAGE/lib/systemd/system/comfy-archive-agent.service"
# Config file: marked as a conffile so local edits survive upgrades.
install -D -m 0644 "$HERE/agent/comfy-archive-agent.conf" \
    "$STAGE/etc/comfy-archive-agent.conf"

# --- Control metadata + maintainer scripts ------------------------------------
mkdir -p "$STAGE/DEBIAN"
sed "s/__VERSION__/$VERSION/" "$HERE/deb/control.template" > "$STAGE/DEBIAN/control"
echo "/etc/comfy-archive-agent.conf" > "$STAGE/DEBIAN/conffiles"
for script in postinst prerm postrm; do
    install -m 0755 "$HERE/deb/$script" "$STAGE/DEBIAN/$script"
done

# --- Build --------------------------------------------------------------------
mkdir -p "$OUT_DIR"
DEB="$OUT_DIR/${PKG}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB"

echo "Built $DEB"
