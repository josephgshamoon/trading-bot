# Polymarket Trading Bot — Claude Context

## Quick Reference
- **Owner**: Joey (joeyquack)
- **Server**: Hostinger VPS, user `clawdbot`
- **Venv**: `venv/bin/python` (NOT system python3)
- **GitHub**: `joeyquack/trading-bot` — token in git remote URL (no expiry)
- **Branch**: `claude/trading-bot-system-NMPLS`

## Architecture
- CLI: `python -m src.main {scan,backtest,paper,live,fast,collect,resolve,calibrate,edge,status,report,stats}`
- Config: `config/default.yaml`, secrets in `.env`
- Live trading uses py-clob-client for Polymarket CLOB on Polygon
- Proxy tunnel (SOCKS5) for geo-blocked region

## Cron Jobs
- `* * * * *` — `cron_fast.sh` (short-term trading: 15-min crypto, tweet brackets)
- `0 * * * *` — `cron_run.sh` (hourly live trading scan)
- `0 */6 * * *` — `scripts/monitor.py` (performance tracking → Supermemory + Telegram)
- `@reboot` — proxy tunnel restore

## Key Files
- `src/strategy/short_term.py` — CryptoMomentumStrategy + TweetBracketStrategy
- `src/engine/live.py` — Full CLOB order execution with session management
- `src/data/journal.py` — JSONL trade journal (cycle + resolution records)
- `src/data/memory.py` — Supermemory integration (BotMemory class)
- `src/data/event_markets.py` — 15-min slot and tweet bracket discovery
- `scripts/monitor.py` — Automated performance tracking cron

## Supermemory
- API key in `.env` as `SUPERMEMORY_API_KEY`
- Container tag: `polymarket-bot`
- Use `BotMemory.recall(topic)` to search past context
- Performance snapshots stored daily with custom_id `perf-snapshot-YYYY-MM-DD`
- Architecture docs seeded with custom_ids: `arch-overview`, `arch-strategies`, etc.

## User Preferences
- Hands-off operation — bot runs autonomously
- Ping only when something needs attention
- Telegram notifications for trade alerts and summaries
