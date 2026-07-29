#!/bin/bash
# Nightly cron wrapper for retag.py
# Logs to _system/runs/retag_cron.log with dated headers.

cd "$(dirname "$0")"
LOG=_system/runs/retag_cron.log
mkdir -p _system/runs

echo "" >> "$LOG"
echo "========================================" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') — retag cron start" >> "$LOG"
echo "========================================" >> "$LOG"

.venv/bin/python3 retag.py --execute >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') — retag cron done" >> "$LOG"
