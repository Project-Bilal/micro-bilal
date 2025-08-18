#!/bin/bash

# Configuration
IMAGE_NAME="esp32-mdns-ota"
FIRMWARE_DIR="firmware"
OUTPUT_DIR="output"

# Remove existing containers
echo "Removing existing containers..."
docker rm $(docker ps -a -q --filter ancestor=$IMAGE_NAME) 2>/dev/null || true

# Remove existing image
echo "Removing existing image..."
docker rmi $IMAGE_NAME 2>/dev/null || true

# Handle output directory
if [ ! -d "$OUTPUT_DIR" ]; then
    echo "Creating output directory..."
    mkdir $OUTPUT_DIR
else
    echo "Cleaning output directory..."
    rm -rf $OUTPUT_DIR/*
fi

# Handle firmware directory
if [ ! -d "$FIRMWARE_DIR" ]; then
    echo "Creating firmware directory..."
    mkdir $FIRMWARE_DIR
else
    echo "Cleaning firmware directory..."
    rm -rf $FIRMWARE_DIR/*
fi

# Build Docker image
echo "Building Docker image..."
docker build --platform linux/amd64 -t $IMAGE_NAME . || { echo "Docker build failed"; exit 1; }

# Run Docker container to build firmware
echo "Running Docker container to build firmware..."
docker run --rm --platform linux/amd64 -v "$PWD/$FIRMWARE_DIR:/firmware" $IMAGE_NAME || { echo "Docker run failed"; exit 1; }

# Ensure firmware.bin exists for flashing
if [ ! -f "$FIRMWARE_DIR/firmware.bin" ]; then
    echo "firmware.bin not found! Cannot flash."
    exit 1
fi

# List files for confirmation
echo "Firmware directory contents:"
ls -la $FIRMWARE_DIR/

# Ask user about flashing
read -p "Do you want to erase and flash firmware.bin to ESP32? (y/n): " flash_choice

if [[ $flash_choice =~ ^[Yy]$ ]]; then
    if ! command -v esptool.py &> /dev/null; then
        echo "esptool.py not found. Install it with: pip install esptool"
        exit 1
    fi

    # Erase and flash
    echo "Erasing flash..."
    esptool.py --port /dev/cu.usbserial-10 erase_flash || { echo "Erase failed"; exit 1; }

    echo "Flashing firmware.bin..."
    esptool.py --chip esp32 --port /dev/cu.usbserial-10 --baud 460800 write_flash -z 0x1000 "$FIRMWARE_DIR/firmware.bin" || {
        echo "Flashing failed"; exit 1;
    }

    echo "Flashing complete! ESP32 is ready."
fi

echo "All operations completed. Firmware files are in $FIRMWARE_DIR/"
