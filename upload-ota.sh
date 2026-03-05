#!/bin/bash
#
# immurok CH592F OTA Upload Script
#
# Usage:
#   ./upload-ota.sh                 # rebuild debug + flash
#   ./upload-ota.sh debug           # rebuild debug + flash
#   ./upload-ota.sh release-debug   # rebuild release-debug + flash
#   ./upload-ota.sh release         # rebuild release + flash
#   ./upload-ota.sh -f              # flash only (no rebuild)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FIRMWARE_BUILD="$PROJECT_DIR/firmware/build"
HEX_FILE="$FIRMWARE_BUILD/immurok_OTA_Combined.hex"
MODE_FILE="$FIRMWARE_BUILD/.ota_build_mode"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse args
FLASH_ONLY=false
MODE=""

case "${1:-}" in
    -f|--flash-only)
        FLASH_ONLY=true
        ;;
    debug|release-debug|release)
        MODE="$1"
        ;;
    "")
        MODE="debug"
        ;;
    *)
        echo "Usage: $0 [debug|release-debug|release|-f]"
        echo ""
        echo "  debug           Rebuild debug + flash (default)"
        echo "  release-debug   Rebuild release-debug + flash"
        echo "  release         Rebuild release + flash"
        echo "  -f              Flash only (skip rebuild)"
        exit 1
        ;;
esac

# Rebuild unless -f
if [ "$FLASH_ONLY" = true ]; then
    if [ ! -f "$HEX_FILE" ]; then
        echo -e "${RED}[ERROR]${NC} No firmware found. Run ./build-ota.sh first."
        exit 1
    fi
    # Show what we're flashing
    if [ -f "$MODE_FILE" ]; then
        echo -e "${CYAN}Flashing existing build [$(cat "$MODE_FILE")]${NC}"
    else
        echo -e "${CYAN}Flashing existing build${NC}"
    fi
else
    "$SCRIPT_DIR/build-ota.sh" "$MODE"
    echo ""
fi

# Flash
echo -e "${GREEN}[FLASH]${NC} Writing to device..."
wlink --chip CH59X --speed high flash "$HEX_FILE"
echo -e "${GREEN}[DONE]${NC} Flash complete."
