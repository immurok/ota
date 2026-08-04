#!/bin/bash
#
# release-web.sh — Build the release .imfw and stage the official-website
# firmware distribution directory (manifest.json + assets), ready to upload.
#
# Usage:
#   ota/release-web.sh              # build VER=6 release, stage ota/web-dist/
#   ota/release-web.sh --no-build   # reuse existing firmware/build/*.imfw
#
# Output: ota/web-dist/
#   manifest.json
#   immurok-ik1-vX.Y.Z.imfw
#   immurok-ik1-v1.6.0-bridge.imfw
#
# Upload web-dist/* to https://immurok.com/fw/ (deployment method depends on
# the website hosting — this script only stages files and prints checksums).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VERSION_H="$PROJECT_DIR/firmware/APP/include/version.h"
IMFW_BUILD="$PROJECT_DIR/firmware/build/immurok_CH592F.imfw"
BRIDGE_SRC="$PROJECT_DIR/firmware/factory/1.6.0/fw-1.6.0.d3ca-r.imfw"
DIST="$SCRIPT_DIR/web-dist"

die() { echo "ERROR: $1" >&2; exit 1; }

[ -f "$BRIDGE_SRC" ] || die "bridge imfw not found: $BRIDGE_SRC"

if [ "${1:-}" != "--no-build" ]; then
    "$SCRIPT_DIR/build-ota.sh" VER=6 release
fi
[ -f "$IMFW_BUILD" ] || die "no .imfw at $IMFW_BUILD (run without --no-build)"

# 版本号从 MAJOR/MINOR/PATCH 数字宏拼接 —— version.h 的 FW_VERSION_STRING 现在
# 是宏派生(编译期由三个数字宏拼接),源码里没有字面量字符串可 sed(2026-08-03 起,
# 避免手写字符串与数字宏不一致)。
_vmaj="$(sed -n 's/.*FW_VERSION_MAJOR[[:space:]]*\([0-9]*\).*/\1/p' "$VERSION_H" | head -1)"
_vmin="$(sed -n 's/.*FW_VERSION_MINOR[[:space:]]*\([0-9]*\).*/\1/p' "$VERSION_H" | head -1)"
_vpat="$(sed -n 's/.*FW_VERSION_PATCH[[:space:]]*\([0-9]*\).*/\1/p' "$VERSION_H" | head -1)"
VER_STR="$_vmaj.$_vmin.$_vpat"
[ -n "$_vmaj" ] && [ -n "$_vmin" ] && [ -n "$_vpat" ] || die "cannot parse FW_VERSION_MAJOR/MINOR/PATCH from $VERSION_H"
SEC_VER="$(sed -n 's/.*FW_SEC_VERSION[[:space:]]*\([0-9]*\).*/\1/p' "$VERSION_H" | head -1)"

mkdir -p "$DIST"
LATEST_NAME="immurok-ik1-v$VER_STR.imfw"
BRIDGE_NAME="immurok-ik1-v1.6.0-bridge.imfw"
cp "$IMFW_BUILD" "$DIST/$LATEST_NAME"
cp "$BRIDGE_SRC" "$DIST/$BRIDGE_NAME"

LATEST_SHA="$(shasum -a 256 "$DIST/$LATEST_NAME" | cut -d' ' -f1)"
BRIDGE_SHA="$(shasum -a 256 "$DIST/$BRIDGE_NAME" | cut -d' ' -f1)"
LATEST_SIZE="$(stat -f%z "$DIST/$LATEST_NAME")"

# latest 的 format 由 .imfw 第 5 字节（header version）决定
FMT_BYTE="$(dd if="$DIST/$LATEST_NAME" bs=1 skip=4 count=1 2>/dev/null | od -An -tu1 | tr -d ' ')"
if [ "$FMT_BYTE" -ge 2 ]; then FORMAT="v2"; else FORMAT="v1"; fi

cat > "$DIST/manifest.json" <<EOF
{
  "schema": 1,
  "latest": {
    "version": "$VER_STR",
    "sec_version": ${SEC_VER:-1},
    "format": "$FORMAT",
    "url": "https://immurok.com/fw/$LATEST_NAME",
    "sha256": "$LATEST_SHA",
    "size": $LATEST_SIZE,
    "min_direct": "1.6.0",
    "notes": ""
  },
  "bridge": {
    "version": "1.6.0",
    "format": "v1",
    "url": "https://immurok.com/fw/$BRIDGE_NAME",
    "sha256": "$BRIDGE_SHA"
  }
}
EOF

echo "Staged $DIST:"
ls -la "$DIST"
echo ""
echo "manifest.json:"
cat "$DIST/manifest.json"
echo ""
echo "NOTE: fill in the English 'notes' field before uploading to https://immurok.com/fw/"
