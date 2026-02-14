#!/bin/bash
# Trading bot cron job — collects snapshots + runs news-enhanced paper trading
# Cron entry: 0 */4 * * * ~/polymarket-bot/trading-bot/cron_run.sh >> ~/polymarket-bot/trading-bot/logs/cron.log 2>&1

cd ~/polymarket-bot/trading-bot || exit 1
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
