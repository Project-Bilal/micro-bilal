#!/bin/bash

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
