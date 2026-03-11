#!/bin/bash
#
# immurok CH592F OTA Build Script (WCH 方式一)
#
# Usage:
#   ./build-ota.sh                 # debug (default)
#   ./build-ota.sh debug           # DEBUG=3, HAL_SLEEP=FALSE
#   ./build-ota.sh release-debug   # DEBUG=3, HAL_SLEEP=TRUE
#   ./build-ota.sh release         # no debug, HAL_SLEEP=TRUE
#   ./build-ota.sh clean           # Clean all builds
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
JUMPAPP_DIR="$PROJECT_DIR/ota/jumpapp"
APP_DIR="$PROJECT_DIR/firmware"
IAP_DIR="$PROJECT_DIR/ota/iap"
OUTPUT_DIR="$APP_DIR/build"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse args
HW_VER=""
MODE=""
for arg in "$@"; do
    case "$arg" in
        --ver=*) HW_VER="${arg#--ver=}" ;;
        VER=*|ver=*) HW_VER="${arg#*=}" ;;
        *) MODE="$arg" ;;
    esac
done
MODE="${MODE:-debug}"
HW_VER="${HW_VER:-0}"

case "$MODE" in
    debug|release-debug|release|clean) ;;
    *)
        echo "Usage: $0 [VER=N] [debug|release-debug|release|clean]"
        echo ""
        echo "  VER=0           Hardware VER0 (default)"
        echo "  VER=1           Hardware VER1"
        echo "  debug           DEBUG + no sleep (default)"
        echo "  release-debug   DEBUG + sleep (for diagnosing sleep issues)"
        echo "  release         No debug, sleep enabled (production)"
        echo "  clean           Clean all builds"
        exit 1
        ;;
esac

# Clean
clean_all() {
    echo_info "Cleaning all builds..."
    (cd "$JUMPAPP_DIR" && make clean 2>/dev/null || true)
    (cd "$APP_DIR" && make clean 2>/dev/null || true)
    (cd "$IAP_DIR" && make clean 2>/dev/null || true)
    echo_info "Clean complete"
}

if [ "$MODE" = "clean" ]; then
    clean_all
    exit 0
fi

echo -e "${CYAN}=== Building OTA firmware [${MODE}] HW=VER${HW_VER} ===${NC}"
echo ""

# Determine make flags for each component
case "$MODE" in
    debug)
        APP_FLAGS="OTA=1 HW_VER=$HW_VER"
        IAP_FLAGS="DEBUG=1"
        ;;
    release-debug)
        APP_FLAGS="OTA=1 RELEASE_DEBUG=1 HW_VER=$HW_VER"
        IAP_FLAGS="DEBUG=1"
        ;;
    release)
        APP_FLAGS="OTA=1 RELEASE=1 HW_VER=$HW_VER"
        IAP_FLAGS=""
        ;;
esac

# Clean first
clean_all
echo ""

# 1. Build JumpIAP
echo_info "Building JumpIAP..."
cd "$JUMPAPP_DIR"
make

size=$(stat -f%z "$JUMPAPP_DIR/build/immurok_JumpIAP.bin" 2>/dev/null || stat -c%s "$JUMPAPP_DIR/build/immurok_JumpIAP.bin" 2>/dev/null)
if [ "$size" -gt 4096 ]; then
    echo_error "JumpIAP too large: $size bytes (max 4096)"
    exit 1
fi
echo_info "JumpIAP size: $size bytes"
echo ""

# 2. Build Application
echo_info "Building Application (OTA, $MODE)..."
cd "$APP_DIR"
make $APP_FLAGS

size=$(stat -f%z "$APP_DIR/build/immurok_CH592F.bin" 2>/dev/null || stat -c%s "$APP_DIR/build/immurok_CH592F.bin" 2>/dev/null)
max_size=$((216 * 1024))
if [ "$size" -gt $max_size ]; then
    echo_error "Application too large: $size bytes (max $max_size)"
    exit 1
fi
echo_info "Application size: $size bytes ($(( size / 1024 ))KB / 216KB)"
echo ""

# 3. Build IAP
echo_info "Building IAP Bootloader..."
cd "$IAP_DIR"
make $IAP_FLAGS

size=$(stat -f%z "$IAP_DIR/build/immurok_IAP.bin" 2>/dev/null || stat -c%s "$IAP_DIR/build/immurok_IAP.bin" 2>/dev/null)
max_size=$((12 * 1024))
if [ "$size" -gt $max_size ]; then
    echo_error "IAP too large: $size bytes (max $max_size)"
    exit 1
fi
echo_info "IAP size: $size bytes ($(( size / 1024 ))KB / 12KB)"
echo ""

# 4. Combine
echo_info "Combining firmware..."
mkdir -p "$OUTPUT_DIR"
output="$OUTPUT_DIR/immurok_OTA_Combined.bin"

dd if=/dev/zero of="$output" bs=1024 count=448 2>/dev/null
dd if="$JUMPAPP_DIR/build/immurok_JumpIAP.bin" of="$output" bs=1 conv=notrunc 2>/dev/null
dd if="$APP_DIR/build/immurok_CH592F.bin" of="$output" bs=1 seek=4096 conv=notrunc 2>/dev/null
dd if="$IAP_DIR/build/immurok_IAP.bin" of="$output" bs=1 seek=446464 conv=notrunc 2>/dev/null

objcopy="${TOOLCHAIN_PATH:-/opt/riscv-wch-gcc}/bin/riscv-wch-elf-objcopy"
if [ -f "$objcopy" ]; then
    "$objcopy" -I binary -O ihex "$output" "$OUTPUT_DIR/immurok_OTA_Combined.hex"
fi

# Save build mode for upload script
echo "$MODE" > "$OUTPUT_DIR/.ota_build_mode"

# 5. Package .imfw (encrypted + signed)
PACKAGE_SCRIPT="$SCRIPT_DIR/ota-package.py"
KEYS_FILE="$SCRIPT_DIR/ota_keys.py"
APP_BIN="$APP_DIR/build/immurok_CH592F.bin"
IMFW_OUTPUT="$OUTPUT_DIR/immurok_CH592F.imfw"

if [ -f "$PACKAGE_SCRIPT" ] && [ -f "$KEYS_FILE" ]; then
    echo_info "Packaging .imfw (encrypted + signed)..."
    python3 "$PACKAGE_SCRIPT" "$APP_BIN" -o "$IMFW_OUTPUT"
    if [ $? -eq 0 ]; then
        imfw_size=$(stat -f%z "$IMFW_OUTPUT" 2>/dev/null || stat -c%s "$IMFW_OUTPUT" 2>/dev/null)
        echo_info ".imfw size: $imfw_size bytes ($(( imfw_size / 1024 ))KB)"
    else
        echo_warn ".imfw packaging failed (OTA keys may be missing)"
    fi
    echo ""
else
    echo_warn "Skipping .imfw packaging (missing ota-package.py or ota_keys.py)"
    echo_warn "Run: python3 ota/generate_ota_keys.py"
    echo ""
fi

echo -e "${CYAN}=== OTA Build Complete [${MODE}] ===${NC}"
echo ""
echo "Flash Layout (V1 - WCH 方式一):"
echo "  0x00000000 - 0x00001000: JumpIAP (4KB)"
echo "  0x00001000 - 0x00037000: Image A / App (216KB)"
echo "  0x00037000 - 0x0006D000: Image B / OTA (216KB)"
echo "  0x0006D000 - 0x00070000: IAP (12KB)"
echo ""
echo "Output files:"
echo "  Combined:  $output"
if [ -f "$IMFW_OUTPUT" ]; then
echo "  OTA (.imfw): $IMFW_OUTPUT"
fi
echo ""
echo "To flash: ota/upload-ota.sh"
echo "To OTA:  python3 ota/ota-update.py $IMFW_OUTPUT"
