FROM --platform=linux/amd64 ubuntu:22.04
LABEL Name=esp32-micropython-image-builder Version=0.0.3

ENV CFLAGS_EXTRA="-Wno-error=maybe-uninitialized"

ENV ESPIDF=/data/esp-idf \
    MICROPYTHON=/data/micropython \
    IDF_TOOLS_PATH=/data/.espressif \
    DEBIAN_FRONTEND=noninteractive

# Update package dependencies and include required packages
RUN apt update && apt -y install \
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
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/python3 /usr/bin/python

# Clean any existing Python environments
RUN rm -rf /data/.espressif/python_env

# Set architecture-specific environment variables
ENV MACHTYPE=x86_64-pc-linux-gnu
ENV HOSTTYPE=x86_64
ENV OSTYPE=linux-gnu

# Create directories
RUN mkdir -p ${ESPIDF} && mkdir -p ${MICROPYTHON}

# Clone ESP-IDF v5.1.1 first and set it up
RUN git clone -b v5.1.1 --recursive https://github.com/espressif/esp-idf.git ${ESPIDF} && \
    cd ${ESPIDF} && \
    git submodule update --init --recursive

# Clean and install ESP-IDF requirements
RUN cd ${ESPIDF} && \
    rm -rf .git && \
    ./install.sh esp32 && \
    . ./export.sh

# Add ESP-IDF to PATH
ENV PATH="${ESPIDF}/tools:${IDF_TOOLS_PATH}/tools/bin:${PATH}"

# Clone MicroPython and set up
RUN git clone https://github.com/micropython/micropython.git ${MICROPYTHON} && \
    cd ${MICROPYTHON} && \
    git checkout v1.22.2 && \
    git submodule update --init --recursive

# Make MicroPython tools
RUN cd ${MICROPYTHON} && \
    make -C mpy-cross

# Set up ESP32 port
RUN cd ${MICROPYTHON}/ports/esp32 && \
    make submodules

# Create modules directory and copy files
RUN mkdir -p ${MICROPYTHON}/ports/esp32/modules/aioble

# Set up modules and aioble library
RUN mkdir -p ${MICROPYTHON}/ports/esp32/modules/aioble && \
    cd ${MICROPYTHON}/ports/esp32/modules && \
    git clone https://github.com/micropython/micropython-lib.git && \
    cp -r micropython-lib/micropython/bluetooth/aioble/aioble/* aioble/ && \
    rm -rf micropython-lib

# Copy your files
COPY main.py utils.py mqtt.py cast.py ble.py ${MICROPYTHON}/ports/esp32/modules/

WORKDIR ${MICROPYTHON}/ports/esp32

# Create build script with explicit target
RUN echo '#!/bin/bash' > /entrypoint.sh && \
    echo 'set -e' >> /entrypoint.sh && \
    echo '. ${ESPIDF}/export.sh' >> /entrypoint.sh && \
    echo 'idf.py fullclean' >> /entrypoint.sh && \
    echo 'make BOARD=ESP32_GENERIC USER_C_MODULES= PYTHON=${IDF_PYTHON:-python3} ESPIDF= CMAKE_PRESETS=\${IDF_TARGET} $@' >> /entrypoint.sh && \
    chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]