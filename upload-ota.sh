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
HW_VER=""

for arg in "$@"; do
    case "$arg" in
        --ver=*) HW_VER="${arg#--ver=}" ;;
        VER=*|ver=*) HW_VER="${arg#*=}" ;;
        -f|--flash-only) FLASH_ONLY=true ;;
        debug|release-debug|release) MODE="$arg" ;;
        *)
            echo "Usage: $0 [VER=N] [debug|release-debug|release|-f]"
            echo ""
            echo "  VER=0           Hardware VER0 (default)"
            echo "  VER=1           Hardware VER1"
            echo "  debug           Rebuild debug + flash (default)"
            echo "  release-debug   Rebuild release-debug + flash"
            echo "  release         Rebuild release + flash"
            echo "  -f              Flash only (skip rebuild)"
            exit 1
            ;;
    esac
done
MODE="${MODE:-debug}"
HW_VER="${HW_VER:-0}"

# Rebuild unless -f
if [ "$FLASH_ONLY" = true ]; then
    if [ ! -f "$HEX_FILE" ]; then
        echo -e "${RED}[ERROR]${NC} No firmware found. Run ./build-ota.sh first."
        exit 1
    fi
    # Show what we're flashing
    FW_VER_FILE="$FIRMWARE_BUILD/.fw_version"
    FW_VER_STR=""
    if [ -f "$FW_VER_FILE" ]; then FW_VER_STR=" FW=$(cat "$FW_VER_FILE")"; fi
    if [ -f "$MODE_FILE" ]; then
        echo -e "${CYAN}Flashing existing build [$(cat "$MODE_FILE")]${FW_VER_STR}${NC}"
    else
        echo -e "${CYAN}Flashing existing build${FW_VER_STR}${NC}"
    fi
else
    "$SCRIPT_DIR/build-ota.sh" --ver="$HW_VER" "$MODE"
    echo ""
fi

# Flash
echo -e "${GREEN}[FLASH]${NC} Writing to device..."
wlink --chip CH59X --speed high flash "$HEX_FILE"
FW_VER_FILE="$FIRMWARE_BUILD/.fw_version"
if [ -f "$FW_VER_FILE" ]; then
    echo -e "${GREEN}[DONE]${NC} Flash complete. FW: $(cat "$FW_VER_FILE")"
else
    echo -e "${GREEN}[DONE]${NC} Flash complete."
fi
