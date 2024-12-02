# syntax=docker/dockerfile:1.4
# Using the modern Docker syntax which provides better caching and more features

# Start with Ubuntu 22.04 and specify platform to ensure consistent builds
# The 'as builder' allows for potential multi-stage builds if needed later
FROM --platform=linux/amd64 ubuntu:22.04 as builder
LABEL Name=esp32-micropython-image-builder Version=0.0.4

# Define versions as build arguments for easy updates without changing the rest of the Dockerfile
# ESP-IDF is the Espressif IoT Development Framework
# MicroPython is the Python implementation for microcontrollers
ARG ESP_IDF_VERSION=v5.1.1
ARG MICROPYTHON_VERSION=v1.22.2

# Environment variables are grouped in a single layer to reduce image size
# CFLAGS_EXTRA: Compiler flags to handle specific warnings
# ESPIDF: Location of the ESP-IDF framework
# MICROPYTHON: Location of the MicroPython source
# IDF_TOOLS_PATH: Where ESP-IDF tools will be installed
# DEBIAN_FRONTEND: Prevents interactive prompts during package installation
# MACHTYPE/HOSTTYPE/OSTYPE: System architecture information
# PATH: Added ESP-IDF tools to system path
# PYTHONUNBUFFERED: Ensures Python output isn't buffered
ENV CFLAGS_EXTRA="-Wno-error=maybe-uninitialized" \
    ESPIDF=/data/esp-idf \
    MICROPYTHON=/data/micropython \
    IDF_TOOLS_PATH=/data/.espressif \
    DEBIAN_FRONTEND=noninteractive \
    MACHTYPE=x86_64-pc-linux-gnu \
    HOSTTYPE=x86_64 \
    OSTYPE=linux-gnu \
    PATH="/data/esp-idf/tools:/data/.espressif/tools/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Install all required system packages in a single RUN command to minimize layers
# Includes development tools, Python environment, and USB utilities
# The cleanup commands at the end reduce the image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    wget \
    flex \
    bison \
    gperf \
    python3 \
    python3-pip \
    python3-setuptools \
    cmake \
    ninja-build \
    ccache \
    libffi-dev \
    libssl-dev \
    dfu-util \
    libusb-1.0-0 \
    python3-venv \
    gcc-multilib \
    g++-multilib \
    libtool \
    make \
    build-essential \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /data/.espressif/python_env

# Create the main working directory
RUN mkdir -p /data
WORKDIR /data

# Clone and setup ESP-IDF framework
# Uses mount cache for pip to speed up builds
# Removes .git directory to reduce image size
RUN --mount=type=cache,target=/root/.cache/pip \
    git clone -b ${ESP_IDF_VERSION} --recursive https://github.com/espressif/esp-idf.git ${ESPIDF} \
    && cd ${ESPIDF} \
    && ./install.sh esp32 \
    && . ./export.sh \
    && rm -rf .git

# Setup MicroPython environment:
# 1. Clone MicroPython repository
# 2. Checkout specific version
# 3. Initialize submodules
# 4. Build mpy-cross (MicroPython cross-compiler)
# 5. Setup ESP32 port
# 6. Add AIOBLE (Async BLE) support from micropython-lib
RUN cd /data \
    && git clone https://github.com/micropython/micropython.git ${MICROPYTHON} \
    && cd ${MICROPYTHON} \
    && git checkout ${MICROPYTHON_VERSION} \
    && git submodule update --init --recursive \
    && make -C mpy-cross \
    && cd ports/esp32 \
    && make submodules \
    && mkdir -p modules/aioble \
    && cd modules \
    && git clone --depth 1 https://github.com/micropython/micropython-lib.git \
    && cp -r micropython-lib/micropython/bluetooth/aioble/aioble/* aioble/ \
    && rm -rf micropython-lib

# Create partition table for OTA updates
# nvs: Non-volatile storage for WiFi credentials and other data
# otadata: OTA update status information
# phy_init: WiFi PHY initialization data
# ota_0/ota_1: Two app partitions for OTA updates
# vfs: Virtual file system for user files
RUN <<EOF
cat > ${MICROPYTHON}/ports/esp32/partitions-ota.csv << 'EOL'
# Name,   Type, SubType, Offset,   Size,     Flags
nvs,      data, nvs,     0x9000,   0x4000,
otadata,  data, ota,     0xd000,   0x2000,
phy_init, data, phy,     0xf000,   0x1000,
ota_0,    app,  ota_0,   0x10000,  0x1C0000,
ota_1,    app,  ota_1,   0x1D0000, 0x1C0000,
vfs,      data, fat,     0x390000, 0x70000,
EOL
EOF

# Configure the custom partition table and enable mDNS in the OTA-specific config
RUN cd ${MICROPYTHON}/ports/esp32 && \
    echo "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=\"partitions-ota.csv\"" >> boards/ESP32_GENERIC/sdkconfig.ota && \
    echo "CONFIG_PARTITION_TABLE_CUSTOM=y" >> boards/ESP32_GENERIC/sdkconfig.ota && \
    echo "CONFIG_MDNS_MAX_SERVICES=10" >> boards/ESP32_GENERIC/sdkconfig.ota && \
    echo "CONFIG_MDNS_SERVICE_ADD_TIMEOUT_MS=2000" >> boards/ESP32_GENERIC/sdkconfig.ota && \
    echo "CONFIG_MDNS_NETWORKING_SOCKET=y" >> boards/ESP32_GENERIC/sdkconfig.ota

# Copy application source files into the MicroPython modules directory
# chmod 644 ensures files are readable but not executable
COPY --chmod=644 source/ ${MICROPYTHON}/ports/esp32/modules/
COPY --chmod=644 ota/ ${MICROPYTHON}/ports/esp32/modules/ota/

# Set the ESP32 port directory as working directory for the build
WORKDIR ${MICROPYTHON}/ports/esp32

# Create the entrypoint script that will:
# 1. Source ESP-IDF environment
# 2. Clean previous builds
# 3. Build MicroPython with OTA support
RUN <<EOF cat > /entrypoint.sh
#!/bin/bash
set -eo pipefail

# Source ESP-IDF environment
. ${ESPIDF}/export.sh

# Clean previous builds
idf.py fullclean

# Execute make with OTA variant and explicit partition table
PARTITION_TABLE_CSV=partitions-ota.csv make BOARD=ESP32_GENERIC BOARD_VARIANT=OTA USER_C_MODULES= PYTHON=${IDF_PYTHON:-python3} ESPIDF= "\$@"
EOF

# Make the entrypoint script executable
RUN chmod +x /entrypoint.sh

# Set the entrypoint script as the container's entrypoint
ENTRYPOINT ["/entrypoint.sh"]