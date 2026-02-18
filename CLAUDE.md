# Polymarket Trading Bot — Claude Context

## Architecture
- CLI: `python -m src.main {scan,backtest,paper,live,fast,collect,resolve,calibrate,edge,status,report,stats}`
- Config: `config/default.yaml`, secrets in `.env`
- Live trading uses py-clob-client for Polymarket CLOB on Polygon
- Optional proxy tunnel (SOCKS5) for geo-blocked regions

## Cron Jobs (optional)
- `* * * * *` — `cron_fast.sh` (short-term trading: 15-min crypto, tweet brackets)
- `0 * * * *` — `cron_run.sh` (hourly live trading scan)
- `0 */6 * * *` — `scripts/monitor.py` (performance tracking)

## Key Files
- `src/strategy/short_term.py` — CryptoMomentumStrategy + TweetBracketStrategy
- `src/engine/live.py` — Full CLOB order execution with session management
- `src/data/journal.py` — JSONL trade journal (cycle + resolution records)
- `src/data/event_markets.py` — 15-min slot and tweet bracket discovery
- `src/data/memory.py` — Optional Supermemory integration (BotMemory class)
- `scripts/monitor.py` — Automated performance tracking cron
