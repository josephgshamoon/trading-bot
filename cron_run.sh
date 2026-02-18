#!/bin/bash
# Trading bot cron job — collects snapshots + runs news-enhanced paper trading
# Usage: crontab -e, then add:
#   0 */4 * * * /path/to/trading-bot/cron_run.sh >> /path/to/trading-bot/logs/cron.log 2>&1

# Resolve the directory this script lives in (works from any cwd)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

source venv/bin/activate

# Load environment variables for API keys
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

echo "========================================"
echo "  Cron run: $(date)"
echo "  Strategy: news_enhanced"
echo "========================================"

echo "[$(date)] Collecting snapshots..."
python -m src.main collect

echo "[$(date)] Running news-enhanced paper trading..."
python -m src.main paper

echo "[$(date)] Done."
echo ""
