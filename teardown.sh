#!/usr/bin/env bash
# teardown.sh — Upload DB, token cache, and latest log back to OneDrive,
# then optionally deallocate the VM.
set -euo pipefail

APP_DIR="$HOME/photo-sorter"
VENV="$APP_DIR/.venv"

echo "========================================"
echo " Photo Sorter — Teardown"
echo "========================================"

echo
echo "[1/2] Uploading _system/ to OneDrive..."
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python3 "$APP_DIR/onedrive_sync.py" --push

echo
echo "[2/2] Done. _system/ is saved to OneDrive."
echo
echo " You can now safely deallocate this VM from the Azure portal."
echo " Your database and logs are preserved in OneDrive."
echo "========================================"
