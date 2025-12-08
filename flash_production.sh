#!/bin/bash

################################################################################
# Production Device Flashing Script
# 
# This script flashes firmware and uploads app files to multiple ESP32 devices.
# Use this for setting up new devices in production.
#
# Usage:
#   1. Connect ESP32 via USB
#   2. Run: ./flash_production.sh
#   3. Follow prompts for each device
#   4. Disconnect and repeat for next device
# IMPORTANT: You must have the firmware.bin already built and in the firmware directory. 
# Use the build_and_flash.sh script to do this and skip the flashing step since it will be flashed in this script.
################################################################################

# Configuration
FIRMWARE_PATH="firmware/firmware.bin"
SOURCE_DIR="source"
DEFAULT_PORT="/dev/cu.usbserial-10"
AMPY_DELAY="1.5"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored status messages
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_header() {
    echo ""
    echo "========================================"
    echo "$1"
    echo "========================================"
    echo ""
}

# Check if required tools are installed
check_requirements() {
    print_status "Checking requirements..."
    
    if ! command -v esptool.py &> /dev/null; then
        print_error "esptool.py not found. Install with: pip install esptool"
        exit 1
    fi
    
    if ! command -v ampy &> /dev/null; then
        print_error "ampy not found. Install with: pip install adafruit-ampy"
        exit 1
    fi
    
    if [ ! -f "$FIRMWARE_PATH" ]; then
        print_error "Firmware file not found: $FIRMWARE_PATH"
        exit 1
    fi
    
    if [ ! -d "$SOURCE_DIR" ]; then
        print_error "Source directory not found: $SOURCE_DIR"
        exit 1
    fi
    
    print_success "All requirements met!"
}

# Detect USB port
detect_port() {
    if [ -e "$DEFAULT_PORT" ]; then
        echo "$DEFAULT_PORT"
        return 0
    fi
    
    # Try to find other common ports
    for port in /dev/cu.usbserial-* /dev/ttyUSB* /dev/cu.SLAB_USBtoUART; do
        if [ -e "$port" ]; then
            echo "$port"
            return 0
        fi
    done
    
    return 1
}

# Flash a single device
flash_device() {
    local port=$1
    local device_num=$2
    
    print_header "DEVICE #$device_num"
    
    print_status "Using port: $port"
    
    # Step 1: Erase flash
    print_status "Step 1/3: Erasing flash..."
    if esptool.py --port "$port" erase_flash 2>&1 | grep -q "Chip erase completed"; then
        print_success "Flash erased"
    else
        print_error "Failed to erase flash"
        return 1
    fi
    
    # Step 2: Flash firmware
    print_status "Step 2/3: Flashing firmware..."
    if esptool.py --chip esp32 --port "$port" --baud 460800 write_flash -z 0x1000 "$FIRMWARE_PATH" 2>&1 | grep -q "Hash of data verified"; then
        print_success "Firmware flashed"
    else
        print_error "Failed to flash firmware"
        return 1
    fi
    
    # Wait for device to boot
    print_status "Waiting 5 seconds for device to boot..."
    sleep 5
    
    # Step 3: Upload app files
    print_status "Step 3/3: Uploading application files..."
    
    local success_count=0
    local fail_count=0
    local failed_files=()
    
    for file in "$SOURCE_DIR"/*.py; do
        if [ -f "$file" ]; then
            filename=$(basename "$file")
            echo -n "  Uploading $filename... "
            
            if ampy --port "$port" --delay "$AMPY_DELAY" put "$file" "/$filename" 2>/dev/null; then
                echo "✓"
                success_count=$((success_count + 1))
            else
                echo "✗"
                fail_count=$((fail_count + 1))
                failed_files+=("$filename")
            fi
        fi
    done
    
    echo ""
    if [ $fail_count -eq 0 ]; then
        print_success "All files uploaded successfully ($success_count files)"
        print_success "Device #$device_num is ready!"
        return 0
    else
        print_warning "Upload completed with errors: $success_count succeeded, $fail_count failed"
        print_warning "Failed files: ${failed_files[*]}"
        return 1
    fi
}

# Main script
main() {
    clear
    print_header "ESP32 PRODUCTION FLASHING TOOL"
    
    echo "This script will flash firmware and upload app files to ESP32 devices."
    echo "Make sure you have your ESP32 connected via USB before continuing."
    echo ""
    
    check_requirements
    
    device_count=0
    
    while true; do
        device_count=$((device_count + 1))
        
        echo ""
        echo "================================================"
        read -p "Ready to flash device #$device_count? (y/n/q to quit): " ready
        
        if [[ $ready =~ ^[Qq]$ ]]; then
            break
        fi
        
        if [[ ! $ready =~ ^[Yy]$ ]]; then
            continue
        fi
        
        # Detect port
        PORT=$(detect_port)
        
        if [ -z "$PORT" ]; then
            print_error "No USB device detected!"
            print_status "Common ports to check:"
            echo "  - macOS: /dev/cu.usbserial-*"
            echo "  - Linux: /dev/ttyUSB*"
            echo ""
            read -p "Enter port manually (or press Enter to skip): " manual_port
            
            if [ -n "$manual_port" ]; then
                PORT="$manual_port"
            else
                device_count=$((device_count - 1))
                continue
            fi
        fi
        
        # Flash the device
        if flash_device "$PORT" "$device_count"; then
            print_success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            print_success "Device #$device_count completed successfully!"
            print_success "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        else
            print_error "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            print_error "Device #$device_count failed!"
            print_error "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            read -p "Retry this device? (y/n): " retry
            if [[ $retry =~ ^[Yy]$ ]]; then
                device_count=$((device_count - 1))
            fi
        fi
        
        echo ""
        print_status "Disconnect current device and connect next device..."
    done
    
    # Summary
    print_header "FLASHING COMPLETE"
    print_success "Total devices processed: $((device_count - 1))"
    echo ""
    echo "All devices are ready for deployment!"
    echo ""
}

# Run main function
main

