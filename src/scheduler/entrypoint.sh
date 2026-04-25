#!/usr/bin/env bash
# Scheduler container entrypoint.
# Installs the crontab, exports env vars for cron jobs, and runs cron in the foreground.
set -euo pipefail

echo "[scheduler] Exporting environment for cron jobs..."
# Cron strips the environment, so dump it to a file each job will source.
printenv | grep -v -E '^(HOME|PATH|PWD|SHLVL|_)=' > /etc/container_env
chmod 644 /etc/container_env

echo "[scheduler] Installing crontab..."
crontab /app/src/scheduler/crontab

echo "[scheduler] Ensuring log file exists..."
touch /var/log/cron.log

echo "[scheduler] Starting cron in foreground (PID 1)..."
# Tail the log in the background so Docker logs surface cron output.
tail -F /var/log/cron.log &

# exec replaces this bash process with cron, making cron PID 1.
# -f = foreground, -L 15 = log everything cron does to syslog/stderr.
exec cron -f -L 15
