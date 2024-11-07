# syntax=docker/dockerfile:1.4
FROM --platform=linux/amd64 ubuntu:22.04 as builder
LABEL Name=esp32-micropython-image-builder Version=0.0.4

# Set ARG for versions to make them easily updatable
ARG ESP_IDF_VERSION=v5.1.1
ARG MICROPYTHON_VERSION=v1.22.2

# Set environment variables in a single layer
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

# Install dependencies in a single layer with cleanup
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

# Create necessary directories
RUN mkdir -p /data

# Set working directory
WORKDIR /data

# Clone and set up ESP-IDF
RUN --mount=type=cache,target=/root/.cache/pip \
    git clone -b ${ESP_IDF_VERSION} --recursive https://github.com/espressif/esp-idf.git ${ESPIDF} \
    && cd ${ESPIDF} \
    && ./install.sh esp32 \
    && . ./export.sh \
    && rm -rf .git

# Clone and set up MicroPython with modules
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

# Create custom partitions file
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

# Configure partition table in multiple locations to ensure it's used
RUN cd ${MICROPYTHON}/ports/esp32 && \
    # Add to main sdkconfig.defaults
    echo "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=\"partitions-ota.csv\"" >> sdkconfig.defaults && \
    echo "CONFIG_PARTITION_TABLE_CUSTOM=y" >> sdkconfig.defaults && \
    # Add to board-specific config
    echo "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=\"partitions-ota.csv\"" >> boards/ESP32_GENERIC/sdkconfig.defaults && \
    echo "CONFIG_PARTITION_TABLE_CUSTOM=y" >> boards/ESP32_GENERIC/sdkconfig.defaults && \
    # Also add to OTA variant config if it exists
    if [ -f boards/ESP32_GENERIC/sdkconfig.ota ]; then \
        echo "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME=\"partitions-ota.csv\"" >> boards/ESP32_GENERIC/sdkconfig.ota && \
        echo "CONFIG_PARTITION_TABLE_CUSTOM=y" >> boards/ESP32_GENERIC/sdkconfig.ota; \
    fi

# Copy application files from source and ota directories
COPY --chmod=644 source/ ${MICROPYTHON}/ports/esp32/modules/
COPY --chmod=644 ota/ ${MICROPYTHON}/ports/esp32/modules/ota/

# Set working directory for build
WORKDIR ${MICROPYTHON}/ports/esp32

# Create build script with enhanced partition table handling
RUN <<EOF cat > /entrypoint.sh
#!/bin/bash
set -eo pipefail

# Source ESP-IDF environment
. ${ESPIDF}/export.sh

# Clean previous builds
idf.py fullclean

# Execute make with OTA variant and explicit partition table
PARTITION_TABLE_CSV=partitions-ota.csv make BOARD=ESP32_GENERIC BOARD_VARIANT=OTA USER_C_MODULES= PYTHON=${IDF_PYTHON:-python3} ESPIDF= "\$@"

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]