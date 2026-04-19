#!/usr/bin/env bash
# Scheduler container entrypoint.
# Installs the crontab and tails the log so Docker sees output.
set -euo pipefail

echo "[scheduler] Installing crontab..."
crontab /app/src/scheduler/crontab

echo "[scheduler] Starting cron..."
cron -f &

# Keep the container alive and surface cron output to Docker logs
touch /var/log/cron.log
tail -f /var/log/cron.log
