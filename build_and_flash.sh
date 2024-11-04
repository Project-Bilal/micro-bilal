#!/bin/bash

# Remove existing containers
echo "Removing existing containers..."
docker rm $(docker ps -a -q --filter ancestor=esp32-image) 2>/dev/null || true

# Remove existing image
echo "Removing existing image..."
docker rmi esp32-image 2>/dev/null || true

# Handle output directory
if [ ! -d "output" ]; then
    echo "Creating output directory..."
    mkdir output
else
    echo "Cleaning output directory..."
    rm -rf output/*
fi

# Build Docker image
echo "Building Docker image..."
docker build --platform linux/amd64 -t esp32-image . || {
    echo "Docker build failed"
    exit 1
}

# Run Docker container
echo "Running Docker container..."
docker run --platform linux/amd64 -v "$PWD/output:/data/micropython/ports/esp32/build-ESP32_GENERIC-OTA/" esp32-image || {
    echo "Docker run failed"
    exit 1
}

# Ask user about flashing
read -p "Do you want to erase and flash the binary to ESP32? (y/n): " flash_choice

if [[ $flash_choice =~ ^[Yy]$ ]]; then
    # List available ports
    echo "Available USB ports:"
    available_ports=($(ls /dev/cu.usbserial* 2>/dev/null))
    
    if [ ${#available_ports[@]} -eq 0 ]; then
        echo "No USB serial ports found"
        exit 1
    fi
    
    # Display ports with numbers
    for i in "${!available_ports[@]}"; do
        echo "$((i+1)): ${available_ports[$i]}"
    done
    
    # Get user's choice
    while true; do
        read -p "Select port number (1-${#available_ports[@]}): " port_num
        if [[ "$port_num" =~ ^[0-9]+$ ]] && [ "$port_num" -ge 1 ] && [ "$port_num" -le "${#available_ports[@]}" ]; then
            break
        fi
        echo "Invalid selection. Please try again."
    done
    
    selected_port="${available_ports[$((port_num-1))]}"
    
    # Erase flash
    echo "Erasing flash..."
    esptool.py --port "$selected_port" erase_flash || {
        echo "Flash erase failed"
        exit 1
    }
    
    # Flash firmware
    echo "Flashing firmware..."
    esptool.py --chip esp32 --port "$selected_port" --baud 460800 write_flash -z 0x1000 output/firmware.bin || {
        echo "Firmware flash failed"
        exit 1
    }
    
    echo "Flashing complete!"
fi

echo "All operations completed successfully!"