#!/bin/bash

# Simple entrypoint to bridge existing docker environment variables to the new
# Python implementation.
set -euo pipefail

if [ "${DEBUG:-false}" = "true" ]; then
  set -x
fi

: "${CONFIG_PATH:=/config/sports.yaml}"
: "${SRC_DIR:=/data/source}"
: "${DEST_DIR:=/data/destination}"
: "${CACHE_DIR:=/data/cache}"
: "${PROCESS_INTERVAL:=0}"
: "${RUN_ONCE:=true}"
: "${DRY_RUN:=false}"

export SPORTS_ORGANIZER_CONFIG="$CONFIG_PATH"
export SPORTS_ORGANIZER_SOURCE="$SRC_DIR"
export SPORTS_ORGANIZER_DESTINATION="$DEST_DIR"
export SPORTS_ORGANIZER_CACHE="$CACHE_DIR"
export SPORTS_ORGANIZER_PROCESS_INTERVAL="$PROCESS_INTERVAL"
export SPORTS_ORGANIZER_RUN_ONCE="$RUN_ONCE"
export SPORTS_ORGANIZER_DRY_RUN="$DRY_RUN"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but not found on PATH" >&2
  exit 1
fi

exec python3 -m sports_organizer.cli "$@"
