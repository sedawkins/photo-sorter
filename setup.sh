#!/usr/bin/env bash
# setup.sh — Provision a fresh Azure Ubuntu VM for photo-sorter.
# Run once after the VM is created. Safe to re-run.
set -euo pipefail

REPO_URL="https://github.com/sedawkins/photo-sorter.git"
APP_DIR="$HOME/photo-sorter"
SYSTEM_DIR="$APP_DIR/_system"
VENV="$APP_DIR/.venv"

echo "========================================"
echo " Photo Sorter — VM Setup"
echo "========================================"

# ── System packages ──────────────────────────────────────────────────────────
echo
echo "[1/5] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv git libheif-dev sqlite3

# ── Repo ─────────────────────────────────────────────────────────────────────
echo
echo "[2/5] Cloning / updating repo..."
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$APP_DIR"
fi

# ── Python environment ────────────────────────────────────────────────────────
echo
echo "[3/5] Setting up Python virtual environment..."
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r "$APP_DIR/requirements.txt"

# ── Config ───────────────────────────────────────────────────────────────────
echo
echo "[4/5] Checking config..."
mkdir -p "$SYSTEM_DIR"

if [ ! -f "$SYSTEM_DIR/config.json" ]; then
    echo
    echo "  No config.json found."
    echo "  Copy config.template.json to _system/config.json and fill in your"
    echo "  SMTP credentials, then re-run this script."
    echo
    cp "$APP_DIR/config.template.json" "$SYSTEM_DIR/config.json"
    echo "  A blank config.json has been placed at: $SYSTEM_DIR/config.json"
    echo "  Edit it now, then re-run setup.sh."
    exit 1
fi

# ── Shell convenience ─────────────────────────────────────────────────────────
grep -qxF "cd ~/photo-sorter" "$HOME/.bashrc" || echo 'cd ~/photo-sorter' >> "$HOME/.bashrc"
grep -qxF "source ~/photo-sorter/.venv/bin/activate" "$HOME/.bashrc" || echo 'source ~/photo-sorter/.venv/bin/activate' >> "$HOME/.bashrc"
grep -qxF "export SORTED_ROOT=" "$HOME/.bashrc" || echo 'export SORTED_ROOT="/Photos/Sorted/Primary"' >> "$HOME/.bashrc"

# ── Systemd service ───────────────────────────────────────────────────────────
echo
echo "[4b] Installing systemd service..."
sudo tee /etc/systemd/system/photo-sorter.service > /dev/null <<EOF
[Unit]
Description=Photo Sorter API
After=network.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
Environment="SORTED_ROOT=/Photos/Sorted/Primary"
ExecStart=$VENV/bin/uvicorn tagger.server:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable photo-sorter

# ── Cron jobs ────────────────────────────────────────────────────────────────
echo
echo "[4c] Installing cron jobs..."
chmod +x "$APP_DIR/cron_retag.sh"
# Add nightly retag at 2am (idempotent — skip if already present)
CRON_LINE="0 2 * * * $APP_DIR/cron_retag.sh"
( crontab -l 2>/dev/null | grep -qxF "$CRON_LINE" ) || \
    ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -

# ── Auth & OneDrive sync ──────────────────────────────────────────────────────
echo
echo "[5/5] Authenticating with Microsoft and syncing _system/ from OneDrive..."
source "$VENV/bin/activate"
python3 "$APP_DIR/onedrive_sync.py" --pull

echo
echo "========================================"
echo " Setup complete. To run the app:"
echo "   source $VENV/bin/activate"
echo "   python3 $APP_DIR/main.py"
echo ""
echo " When finished, run teardown.sh to save"
echo " your database and logs to OneDrive."
echo "========================================"
