#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DIR}/.." && pwd)"
VENV_PATH="${PROJECT_ROOT}/.venv"

echo "[bootstrap] Project root: ${PROJECT_ROOT}"
echo "[bootstrap] Using virtual environment at: ${VENV_PATH}"

if [[ ! -d "${VENV_PATH}" ]]; then
  echo "[bootstrap] Creating virtual environment..."
  python3 -m venv "${VENV_PATH}"
else
  echo "[bootstrap] Virtual environment already exists, reusing."
fi

source "${VENV_PATH}/bin/activate"

echo "[bootstrap] Upgrading pip..."
pip install --upgrade pip

if [[ -f "${PROJECT_ROOT}/requirements.txt" ]]; then
  echo "[bootstrap] Installing requirements.txt..."
  pip install -r "${PROJECT_ROOT}/requirements.txt"
else
  echo "[bootstrap] Skipping requirements.txt (not found)."
fi

if [[ -f "${PROJECT_ROOT}/requirements-dev.txt" ]]; then
  echo "[bootstrap] Installing requirements-dev.txt..."
  pip install -r "${PROJECT_ROOT}/requirements-dev.txt"
else
  echo "[bootstrap] Skipping requirements-dev.txt (not found)."
fi

echo "[bootstrap] Running tests with pytest..."
python3 -m pytest "$@"

