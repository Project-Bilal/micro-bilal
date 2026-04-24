#!/bin/bash

################################################################################
# Flash Device Script
#
# Non-interactive script to flash firmware and upload app files to an ESP32.
# Automatically installs prerequisites on first run.
#
# Usage:
#   ./flash_device.sh              # Auto-detect USB port
#   ./flash_device.sh /dev/ttyUSB0 # Use specific port
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRMWARE_PATH="$SCRIPT_DIR/firmware/firmware.bin"
SOURCE_DIR="$SCRIPT_DIR/source"
AMPY_DELAY="1.5"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }

########################################
# Phase 1: Environment check
########################################

needs_logout=false

# Install esptool if missing
if ! command -v esptool.py &> /dev/null; then
    info "Installing esptool..."
    pip3 install esptool
    success "esptool installed"
fi

# Install ampy if missing
if ! command -v ampy &> /dev/null; then
    info "Installing adafruit-ampy..."
    pip3 install adafruit-ampy
    success "ampy installed"
fi

# Check dialout group (Linux only)
if [[ "$OSTYPE" != "darwin"* ]]; then
    if ! groups "$USER" | grep -qw dialout; then
        info "Adding $USER to dialout group for serial port access..."
        sudo usermod -a -G dialout "$USER"
        warn "You were added to the 'dialout' group."
        warn "Please log out and log back in, then run this script again."
        exit 0
    fi
fi

# Check firmware and source exist
if [ ! -f "$FIRMWARE_PATH" ]; then
    error "Firmware not found: $FIRMWARE_PATH"
    exit 1
fi

if [ ! -d "$SOURCE_DIR" ]; then
    error "Source directory not found: $SOURCE_DIR"
    exit 1
fi

########################################
# Phase 2: Detect port
########################################

detect_port() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        local patterns=(/dev/cu.usbserial* /dev/cu.wchusbserial* /dev/cu.usbmodem* /dev/cu.SLAB_USBtoUART*)
    else
        local patterns=(/dev/ttyUSB* /dev/ttyACM* /dev/ttyAMA*)
    fi

    for pattern in "${patterns[@]}"; do
        for port in $pattern; do
            if [ -e "$port" ] && [ ! -d "$port" ]; then
                echo "$port"
                return 0
            fi
        done
    done
    return 1
}

if [ $# -ge 1 ]; then
    PORT="$1"
    if [ ! -e "$PORT" ]; then
        error "Port does not exist: $PORT"
        exit 1
    fi
else
    PORT=$(detect_port) || {
        error "No ESP32 detected. Make sure the device is plugged in via USB."
        error "You can also specify the port manually: ./flash_device.sh /dev/ttyUSB0"
        exit 1
    }
fi

info "Using port: $PORT"

########################################
# Phase 3: Flash
########################################

echo ""
info "Step 1/3: Erasing flash..."
if esptool.py --port "$PORT" erase_flash 2>&1 | grep -q "Chip erase completed"; then
    success "Flash erased"
else
    error "Failed to erase flash"
    exit 1
fi

info "Step 2/3: Flashing firmware..."
if esptool.py --chip esp32 --port "$PORT" --baud 460800 write_flash -z 0x1000 "$FIRMWARE_PATH" 2>&1 | grep -q "Hash of data verified"; then
    success "Firmware flashed"
else
    error "Failed to flash firmware"
    exit 1
fi

info "Waiting for device to boot..."
sleep 5

info "Step 3/3: Uploading application files..."
fail_count=0
for file in "$SOURCE_DIR"/*.py; do
    if [ -f "$file" ]; then
        filename=$(basename "$file")
        echo -n "  $filename... "
        if ampy --port "$PORT" --delay "$AMPY_DELAY" put "$file" "/$filename" 2>/dev/null; then
            echo "ok"
        else
            echo "FAILED"
            fail_count=$((fail_count + 1))
        fi
    fi
done

echo ""
if [ $fail_count -eq 0 ]; then
    success "========================================"
    success "  Device flashed successfully!"
    success "========================================"
    exit 0
else
    error "========================================"
    error "  Flashing completed with $fail_count file upload failure(s)"
    error "========================================"
    exit 1
fi
