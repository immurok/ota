#!/bin/bash
#
# release-github.sh — Build the general/release .imfw and publish it to the
# immurok/firmware GitHub Release.
#
# Local one-command release (NOT CI): building .imfw needs the non-public
# CH592F SDK + the OTA signing/encryption keys, which only exist on the dev
# machine — so the build runs locally and only the resulting .imfw is uploaded
# to GitHub via `gh`. The artifact lives as a Release asset, never in the git
# tree (that's why dist/ was removed from the public repo).
#
# Usage:
#   ota/release-github.sh            # build VER=6 general release, publish
#   ota/release-github.sh VER=5      # override hardware version (passthrough)
#
# Requires: gh CLI (authenticated), and the normal local build env
# (TOOLCHAIN_PATH set, firmware/SDK/ in place, ota_keys present).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO="immurok/firmware"
VERSION_H="$PROJECT_DIR/firmware/APP/include/version.h"
IMFW_BUILD="$PROJECT_DIR/firmware/build/immurok_CH592F.imfw"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[REL]${NC} $1"; }
warn() { echo -e "${YELLOW}[REL]${NC} $1"; }
die()  { echo -e "${RED}[REL] ERROR:${NC} $1" >&2; exit 1; }

# 1. Preflight
command -v gh >/dev/null       || die "gh CLI not found (brew install gh)"
gh auth status >/dev/null 2>&1 || die "gh not authenticated (run: gh auth login)"
[ -f "$VERSION_H" ]            || die "version.h not found: $VERSION_H"

# Optional VER=N passthrough; default VER=6 general release.
VER_ARG="VER=6"
for arg in "$@"; do
    case "$arg" in
        VER=*|ver=*|--ver=*) VER_ARG="$arg" ;;
        *) die "unsupported arg '$arg' (only VER=N is accepted)" ;;
    esac
done

# 2. Version + tag (from version.h)
VER_STR="$(sed -n 's/.*FW_VERSION_STRING[[:space:]]*"\([0-9.]*\)".*/\1/p' "$VERSION_H")"
[ -n "$VER_STR" ] || die "could not parse FW_VERSION_STRING from $VERSION_H"
TAG="fw-v$VER_STR"
HASH="$(cd "$PROJECT_DIR" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
ASSET_NAME="immurok-ik1-v$VER_STR.imfw"

info "version $VER_STR (build $HASH) -> tag $TAG, asset $ASSET_NAME"

# 3. Build (general SKU / release)
info "building $VER_ARG release (general SKU)..."
"$SCRIPT_DIR/build-ota.sh" "$VER_ARG" release
[ -f "$IMFW_BUILD" ] || die "build did not produce $IMFW_BUILD"

# 4. Stage asset under the release name
ASSET_PATH="$(dirname "$IMFW_BUILD")/$ASSET_NAME"
cp "$IMFW_BUILD" "$ASSET_PATH"
SIZE="$(stat -f%z "$ASSET_PATH" 2>/dev/null || stat -c%s "$ASSET_PATH" 2>/dev/null || echo 0)"
info "asset $ASSET_NAME ($((SIZE / 1024))KB)"

# 5. Publish (idempotent: create the release, or re-upload the asset)
NOTES="immurok IK-1 firmware v$VER_STR (build $HASH) — general SKU, release build, VER6."
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    info "release $TAG exists -> uploading asset (clobber)"
    gh release upload "$TAG" "$ASSET_PATH" --repo "$REPO" --clobber
else
    info "creating release $TAG"
    warn "tag is created at immurok/firmware HEAD — run 'scripts/sync-github.sh firmware' first if you want the published source to match this .imfw"
    gh release create "$TAG" "$ASSET_PATH" --repo "$REPO" --title "Firmware v$VER_STR" --notes "$NOTES"
fi

URL="$(gh release view "$TAG" --repo "$REPO" --json url -q .url 2>/dev/null || true)"
info "done: ${URL:-published $TAG to $REPO}"
