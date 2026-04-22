#!/bin/bash

################################################################################
# Make Flash Kit
#
# Packages everything needed to flash ESP32 devices into a single zip file.
# Send flash_kit.zip to anyone who needs to flash devices — they unzip it,
# run flash_device.sh, and they're good to go.
#
# Contents of the zip:
#   firmware/           - Pre-built firmware binaries
#   source/             - Python application files uploaded to the device
#   flash_device.sh     - Non-interactive script that flashes a single device
#   PRODUCTION_SETUP.txt - Instructions for the person doing the flashing
#
# Usage:
#   ./make_flash_kit.sh
################################################################################

set -euo pipefail

ZIP_NAME="flash_kit.zip"

REQUIRED_PATHS=(
  "firmware"
  "flash_device.sh"
  "source"
  "PRODUCTION_SETUP.txt"
)

for path in "${REQUIRED_PATHS[@]}"; do
  if [ ! -e "$path" ]; then
    echo "Missing required path: $path"
    exit 1
  fi
done

rm -f "$ZIP_NAME"

zip -r "$ZIP_NAME" \
  firmware \
  flash_device.sh \
  source \
  PRODUCTION_SETUP.txt \
  -x "source/.DS_Store" "source/.workflow-trigger"

echo "Created $ZIP_NAME successfully."
