#!/bin/bash

# Script to upload application files to ESP32 filesystem
# Usage: ./upload_app.sh [port]
# Example: ./upload_app.sh /dev/cu.usbserial-10

# Configuration
DEFAULT_PORT="/dev/cu.usbserial-10"
SOURCE_DIR="source"
AMPY_DELAY="1.5"  # Delay between operations for stability

# Use provided port or default
PORT="${1:-$DEFAULT_PORT}"

echo "=================================================="
echo "ESP32 Application File Uploader"
echo "=================================================="
echo "Port: $PORT"
echo "Source directory: $SOURCE_DIR"
echo "=================================================="
echo ""

# Check if ampy is installed
if ! command -v ampy &> /dev/null; then
    echo "ERROR: ampy is not installed"
    echo "Install it with: pip install adafruit-ampy"
    exit 1
fi

# Check if source directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo "ERROR: Source directory '$SOURCE_DIR' not found"
    exit 1
fi

# Check if port exists
if [ ! -e "$PORT" ]; then
    echo "ERROR: Port '$PORT' not found"
    echo "Available ports:"
    ls -1 /dev/cu.* 2>/dev/null || ls -1 /dev/ttyUSB* 2>/dev/null || echo "No serial ports found"
    exit 1
fi

# Function to upload a file
upload_file() {
    local file=$1
    local filename=$(basename "$file")
    local dest_path="/$filename"
    
    echo -n "Uploading $filename... "
    if ampy --port "$PORT" --delay "$AMPY_DELAY" put "$file" "$dest_path" 2>/dev/null; then
        echo "✓"
        return 0
    else
        echo "✗ FAILED"
        return 1
    fi
}

# Upload all .py files from source directory
echo "Starting upload..."
echo ""

success_count=0
fail_count=0
total_files=0

for file in "$SOURCE_DIR"/*.py; do
    if [ -f "$file" ]; then
        total_files=$((total_files + 1))
        if upload_file "$file"; then
            success_count=$((success_count + 1))
        else
            fail_count=$((fail_count + 1))
        fi
    fi
done

echo ""
echo "=================================================="
echo "Upload Summary"
echo "=================================================="
echo "Total files: $total_files"
echo "Successful: $success_count"
echo "Failed: $fail_count"
echo "=================================================="
echo ""

# List files on device
if [ $success_count -gt 0 ]; then
    echo "Files on device:"
    echo "=================================================="
    ampy --port "$PORT" --delay "$AMPY_DELAY" ls / 2>/dev/null || echo "Could not list files"
    echo "=================================================="
fi

# Exit with error if any uploads failed
if [ $fail_count -gt 0 ]; then
    echo ""
    echo "⚠️  Some files failed to upload!"
    echo "Try:"
    echo "  1. Unplug and replug the device"
    echo "  2. Close any programs using the serial port (Thonny, screen, etc.)"
    echo "  3. Run this script again"
    exit 1
fi

echo ""
echo "✓ All files uploaded successfully!"
echo ""
echo "Next steps:"
echo "  1. Connect to device: screen $PORT 115200"
echo "  2. Press Ctrl+D to reboot"
echo "  3. Press Ctrl+A then K to exit screen"
echo ""

exit 0

