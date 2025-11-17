#!/bin/bash

# Simple entrypoint to bridge existing docker environment variables to the new
# Python implementation.
set -euo pipefail

if [ "${DEBUG:-false}" = "true" ]; then
  set -x
fi

: "${CONFIG_PATH:=/config/playbook.yaml}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but not found on PATH" >&2
  exit 1
fi

read_config_setting() {
  local config_path="$1"
  local key="$2"
  python3 - "$config_path" "$key" <<'PY'
import sys
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
setting_key = sys.argv[2]

if not config_path.exists():
    sys.exit(2)

try:
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
except yaml.YAMLError as exc:
    print(f"{exc}", file=sys.stderr)
    sys.exit(4)

settings = data.get("settings")
if not isinstance(settings, dict):
    sys.exit(3)

value = settings.get(setting_key)
if value in (None, "", [], {}):
    sys.exit(1)

print(value)
PY
}

require_path_setting() {
  local env_name="$1"
  local config_key="$2"
  local current_value="${!env_name:-}"

  if [ -n "$current_value" ]; then
    export "$env_name"="$current_value"
    return
  fi

  local config_value
  local config_status=0
  config_value=$(read_config_setting "$CONFIG_PATH" "$config_key") || config_status=$?

  if [ "$config_status" -eq 0 ]; then
    if [[ "$config_value" == *'${'* || "$config_value" == *'$'* ]]; then
      echo "ERROR: Environment variable $env_name is not set and configuration value 'settings.$config_key' in $CONFIG_PATH relies on unresolved environment placeholders." >&2
      echo "Please set $env_name before starting the container." >&2
      exit 1
    fi

    export "$env_name"="$config_value"
    return
  fi

  case "$config_status" in
    1)
      echo "ERROR: Environment variable $env_name is not set and 'settings.$config_key' is missing or empty in $CONFIG_PATH." >&2
      ;;
    2)
      echo "ERROR: Environment variable $env_name is not set and configuration file $CONFIG_PATH does not exist." >&2
      ;;
    3)
      echo "ERROR: Environment variable $env_name is not set and $CONFIG_PATH lacks a 'settings' section." >&2
      ;;
    4)
      echo "ERROR: Failed to parse $CONFIG_PATH while looking for 'settings.$config_key'." >&2
      ;;
    *)
      echo "ERROR: Unable to resolve $env_name or configuration setting 'settings.$config_key' (exit code $config_status)." >&2
      ;;
  esac
  echo "Please set $env_name or define 'settings.$config_key' in $CONFIG_PATH." >&2
  exit 1
}

require_path_setting "SOURCE_DIR" "source_dir"
require_path_setting "DESTINATION_DIR" "destination_dir"
require_path_setting "CACHE_DIR" "cache_dir"

: "${PROCESS_INTERVAL:=0}"
: "${RUN_ONCE:=true}"
: "${DRY_RUN:=false}"

export CONFIG_PATH SOURCE_DIR DESTINATION_DIR CACHE_DIR PROCESS_INTERVAL RUN_ONCE DRY_RUN

exec python3 -m playbook.cli "$@"
