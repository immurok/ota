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
MODE_FILE="$FIRMWARE_BUILD/.ota_build_mode"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse args
FLASH_ONLY=false
MODE=""
HW_VER=""
FW_VARIANT=""

for arg in "$@"; do
    case "$arg" in
        --ver=*) HW_VER="${arg#--ver=}" ;;
        VER=*|ver=*) HW_VER="${arg#*=}" ;;
        # Optional color variant. Forwarded to build-ota.sh; ignored in -f
        # (flash-only) mode since the existing hex already encodes whatever
        # variant the last build produced.
        --variant=*) FW_VARIANT="${arg#--variant=}" ;;
        VARIANT=*|variant=*) FW_VARIANT="${arg#*=}" ;;
        -f|--flash-only) FLASH_ONLY=true ;;
        debug|release-debug|release) MODE="$arg" ;;
        *)
            echo "Usage: $0 [VER=N] [VARIANT=W|B|G] [debug|release-debug|release|-f]"
            echo ""
            echo "  VER=0           Hardware VER0 (original prototype)"
            echo "  VER=1           Hardware VER1 (Rev.1 board)"
            echo "  VER=2           Hardware VER2 (Rev.2 board, ZW3021)"
            echo "  VER=3           Hardware VER3 (Rev.2 board + R599S module)"
            echo "  VER=5           Hardware VER5 (Rev.3 board — default, latest)"
            echo "  VARIANT=W|B|G   Color SKU; omit for general SKU (DIS \"IK-1\")"
            echo "  debug           Rebuild debug + flash (default)"
            echo "  release-debug   Rebuild release-debug + flash"
            echo "  release         Rebuild release + flash"
            echo "  -f              Flash only (skip rebuild)"
            exit 1
            ;;
    esac
done
MODE="${MODE:-debug}"
HW_VER="${HW_VER:-5}"

HEX_FILE="$FIRMWARE_BUILD/immurok_OTA_Combined.hex"

if [ "$FLASH_ONLY" = true ]; then
    if [ ! -f "$HEX_FILE" ]; then
        echo -e "${RED}[ERROR]${NC} No firmware found at $HEX_FILE. Run ./build-ota.sh first."
        exit 1
    fi
    FW_VER_FILE="$FIRMWARE_BUILD/.fw_version"
    FW_VER_STR=""
    if [ -f "$FW_VER_FILE" ]; then FW_VER_STR=" FW=$(cat "$FW_VER_FILE")"; fi
    if [ -f "$MODE_FILE" ]; then
        echo -e "${CYAN}Flashing existing build [$(cat "$MODE_FILE")]${FW_VER_STR}${NC}"
    else
        echo -e "${CYAN}Flashing existing build${FW_VER_STR}${NC}"
    fi
else
    BUILD_ARGS=(--ver="$HW_VER" "$MODE")
    if [ -n "$FW_VARIANT" ]; then
        BUILD_ARGS+=(--variant="$FW_VARIANT")
    fi
    "$SCRIPT_DIR/build-ota.sh" "${BUILD_ARGS[@]}"
    echo ""
fi

# Flash
echo -e "${GREEN}[FLASH]${NC} Writing to device..."
wlink --chip CH59X --speed medium flash "$HEX_FILE"
wlink --chip CH59X reset
FW_VER_FILE="$FIRMWARE_BUILD/.fw_version"
if [ -f "$FW_VER_FILE" ]; then
    echo -e "${GREEN}[DONE]${NC} Flash complete. FW: $(cat "$FW_VER_FILE")"
else
    echo -e "${GREEN}[DONE]${NC} Flash complete."
fi
