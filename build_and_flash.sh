#!/bin/bash
# Build MicroPython firmware and flash it to the ESP32 device.

set -e

# Configuration
IMAGE_NAME="esp32-mdns-ota"
FIRMWARE_DIR="firmware"
SOURCE_DIR="source"

# Auto-detect ESP32 serial port
detect_port() {
    local port
    port=$(ls /dev/cu.usbserial-* 2>/dev/null | head -1)
    if [ -z "$port" ]; then
        echo "ERROR: No ESP32 found. Plug in the device and try again."
        exit 1
    fi
    echo "$port"
}

PORT=$(detect_port)
echo "Detected ESP32 on: $PORT"

# Clean up old Docker artifacts
echo "Cleaning up old Docker artifacts..."
docker rm $(docker ps -a -q --filter ancestor=$IMAGE_NAME) 2>/dev/null || true
docker rmi $IMAGE_NAME 2>/dev/null || true

# Prepare directories
rm -rf "$FIRMWARE_DIR" && mkdir "$FIRMWARE_DIR"

# Build firmware
echo "Building Docker image (this may take a while)..."
docker build --platform linux/amd64 -t $IMAGE_NAME . || { echo "Docker build failed"; exit 1; }

echo "Building firmware..."
docker run --rm --platform linux/amd64 -v "$PWD/$FIRMWARE_DIR:/firmware" $IMAGE_NAME || { echo "Firmware build failed"; exit 1; }

if [ ! -f "$FIRMWARE_DIR/firmware.bin" ]; then
    echo "ERROR: firmware.bin not found after build"
    exit 1
fi

echo "Firmware built successfully:"
ls -lh "$FIRMWARE_DIR/firmware.bin"

# Flash firmware
if ! command -v esptool.py &> /dev/null; then
    echo "ERROR: esptool.py not found. Install with: pip install esptool"
    exit 1
fi

echo "Erasing flash..."
esptool.py --port "$PORT" erase_flash

echo "Flashing firmware..."
esptool.py --chip esp32 --port "$PORT" --baud 460800 write_flash -z 0x1000 "$FIRMWARE_DIR/firmware.bin"

echo "Flashing complete. Waiting for device to boot..."
sleep 5

# Upload application files
echo "Uploading application files..."

# Kill any mpremote/ampy processes that might hold the port
pkill -f mpremote 2>/dev/null || true
sleep 1

success=0
fail=0
for file in "$SOURCE_DIR"/*.py; do
    [ -f "$file" ] || continue
    filename=$(basename "$file")
    echo -n "  $filename... "
    if python3 -m mpremote connect "$PORT" cp "$file" ":$filename" 2>/dev/null; then
        echo "ok"
        success=$((success + 1))
    else
        echo "FAILED"
        fail=$((fail + 1))
    fi
    sleep 0.5
done

echo ""
echo "Upload complete: $success succeeded, $fail failed"

# Reset device
echo "Resetting device..."
python3 -m mpremote connect "$PORT" reset 2>/dev/null || true

echo ""
echo "Done! Device should be booting with new firmware."
echo "Connect with: screen $PORT 115200"
