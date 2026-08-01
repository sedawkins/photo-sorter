#!/bin/bash
# Nightly cron wrapper for retag.py
# Logs to _system/runs/retag_cron.log with dated headers.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$SCRIPT_DIR/_system/runs/retag_cron.log"
CANARY=/tmp/cron_retag_canary.log

echo "$(date '+%Y-%m-%d %H:%M:%S') — started SCRIPT_DIR=$SCRIPT_DIR" >> "$CANARY"
mkdir -p "$SCRIPT_DIR/_system/runs"

echo "" >> "$LOG"
echo "========================================" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') — retag cron start" >> "$LOG"
echo "========================================" >> "$LOG"

cd "$SCRIPT_DIR"
.venv/bin/python3 retag.py --execute >> "$LOG" 2>&1
EXIT=$?

echo "$(date '+%Y-%m-%d %H:%M:%S') — retag cron done (exit $EXIT)" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') — done exit=$EXIT" >> "$CANARY"
