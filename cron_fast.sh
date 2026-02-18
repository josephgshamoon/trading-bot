#!/bin/bash
# Fast-cycle trading — runs every minute for short-term markets.
# Targets: 15-min crypto up/down, Elon tweet brackets.
#
# Cron entry (update paths to match your setup):
#   * * * * * /path/to/trading-bot/cron_fast.sh >> /path/to/trading-bot/logs/fast.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# ── Lockfile — prevent concurrent runs ──────────────────────────
LOCKFILE="$SCRIPT_DIR/.fast_cycle.lock"
if [ -f "$LOCKFILE" ]; then
    LOCK_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "[$(date)] Skipping — previous cycle still running (PID $LOCK_PID)"
        exit 0
    else
        echo "[$(date)] Stale lock removed (PID $LOCK_PID no longer running)"
        rm -f "$LOCKFILE"
    fi
fi
echo $$ > "$LOCKFILE"
trap "rm -f '$LOCKFILE'" EXIT

source venv/bin/activate

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Ensure proxy tunnel (if using one)
if [ -f "$SCRIPT_DIR/scripts/ensure_tunnel.sh" ]; then
    bash "$SCRIPT_DIR/scripts/ensure_tunnel.sh" 2>/dev/null
fi

# Rotate log if > 2 MB
LOG_FILE="$SCRIPT_DIR/logs/fast.log"
if [ -f "$LOG_FILE" ]; then
    LOG_SIZE=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt 2097152 ]; then
        mv "$LOG_FILE" "${LOG_FILE}.old"
    fi
fi

echo "[$(date)] Fast cycle starting..."
python -m src.main fast 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date)] ERROR: fast exited with code $EXIT_CODE"
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -H "Content-Type: application/json" \
            -d "{\"chat_id\":\"$TELEGRAM_CHAT_ID\",\"text\":\"Warning: Fast cycle error (exit $EXIT_CODE)\"}" > /dev/null 2>&1
    fi
fi

echo "[$(date)] Done."
echo ""
