#!/bin/bash
# Trading bot cron job — collects snapshots + runs paper trading
# Cron entry: 0 */4 * * * ~/polymarket-bot/trading-bot/cron_run.sh >> ~/polymarket-bot/trading-bot/logs/cron.log 2>&1

cd ~/polymarket-bot/trading-bot || exit 1
source venv/bin/activate

echo "========================================"
echo "  Cron run: $(date)"
echo "========================================"

echo "[$(date)] Collecting snapshots..."
python -m src.main collect

echo "[$(date)] Running paper trading..."
python -m src.main paper

echo "[$(date)] Done."
echo ""
