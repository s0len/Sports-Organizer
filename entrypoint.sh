#!/bin/bash

# Simple entrypoint to bridge existing docker environment variables to the new
# Python implementation.
set -euo pipefail

if [ "${DEBUG:-false}" = "true" ]; then
  set -x
fi

: "${CONFIG_PATH:=/config/sports.yaml}"
: "${SOURCE_DIR:=/data/source}"
: "${DESTINATION_DIR:=/data/destination}"
: "${CACHE_DIR:=/data/cache}"
: "${PROCESS_INTERVAL:=0}"
: "${RUN_ONCE:=true}"
: "${DRY_RUN:=false}"

export CONFIG_PATH SOURCE_DIR DESTINATION_DIR CACHE_DIR PROCESS_INTERVAL RUN_ONCE DRY_RUN

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but not found on PATH" >&2
  exit 1
fi

exec python3 -m sports_organizer.cli "$@"
