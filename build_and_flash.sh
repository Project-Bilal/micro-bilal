#!/bin/bash

# Configuration
IMAGE_NAME="esp32-mdns-ota"
FIRMWARE_DIR="firmware"
OUTPUT_DIR="output"
SOURCE_DIR="source"
DEFAULT_PORT="/dev/cu.usbserial-10"
AMPY_DELAY="1.5"

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
    
    # Wait for device to boot
    echo ""
    echo "Waiting 5 seconds for device to boot..."
    sleep 5
    
    # Ask about uploading app files
    echo ""
    read -p "Do you want to upload application files to ESP32 filesystem? (y/n): " upload_choice
    
    if [[ $upload_choice =~ ^[Yy]$ ]]; then
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
        
        echo ""
        echo "=================================================="
        echo "Uploading Application Files"
        echo "=================================================="
        echo "Port: $DEFAULT_PORT"
        echo "Source directory: $SOURCE_DIR"
        echo "=================================================="
        echo ""
        
        # Upload all .py files
        success_count=0
        fail_count=0
        total_files=0
        
        for file in "$SOURCE_DIR"/*.py; do
            if [ -f "$file" ]; then
                total_files=$((total_files + 1))
                filename=$(basename "$file")
                
                echo -n "Uploading $filename... "
                if ampy --port "$DEFAULT_PORT" --delay "$AMPY_DELAY" put "$file" "/$filename" 2>/dev/null; then
                    echo "✓"
                    success_count=$((success_count + 1))
                else
                    echo "✗ FAILED"
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
        
        if [ $fail_count -gt 0 ]; then
            echo ""
            echo "⚠️  Some files failed to upload!"
            echo "Try: ./upload_app.sh to retry just the file upload"
        else
            echo ""
            echo "✓ All application files uploaded successfully!"
        fi
        
        # List files on device
        if [ $success_count -gt 0 ]; then
            echo ""
            echo "Files on device:"
            echo "=================================================="
            ampy --port "$DEFAULT_PORT" --delay "$AMPY_DELAY" ls / 2>/dev/null || echo "Could not list files"
            echo "=================================================="
        fi
    fi
fi

echo ""
echo "All operations completed!"
echo "  - Firmware files are in $FIRMWARE_DIR/"
echo "  - To connect to device: screen $DEFAULT_PORT 115200"
echo "  - To retry app upload: ./upload_app.sh"
