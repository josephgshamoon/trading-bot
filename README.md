# Polymarket Trading Bot

Automated trading bot for [Polymarket](https://polymarket.com) prediction markets. Scans for mispriced markets, analyzes news with LLMs, and executes trades via the CLOB API.

Supports **paper trading** (simulated) and **live trading** (real USDC on Polygon).

---

## Quick Start (5 minutes)

### 1. Clone and install

```bash
git clone https://github.com/joeyquack/trading-bot.git
cd trading-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up your API keys

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

| Key | Required? | Where to get it |
|-----|-----------|-----------------|
| `POLYMARKET_API_KEY` | Yes | [docs.polymarket.com](https://docs.polymarket.com) |
| `POLYMARKET_API_SECRET` | Yes | Same (or run `python scripts/setup_api_creds.py`) |
| `POLYMARKET_PASSPHRASE` | Yes | Same as above |
| `POLYMARKET_PRIVATE_KEY` | For live trading | Your wallet private key |
| `ANTHROPIC_API_KEY` | Recommended | [console.anthropic.com](https://console.anthropic.com) |
| `OPENAI_API_KEY` | Optional | [platform.openai.com](https://platform.openai.com) (fallback LLM) |
| `NEWSAPI_KEY` | Optional | [newsapi.org](https://newsapi.org) (free: 100 req/day) |
| `GNEWS_API_KEY` | Optional | [gnews.io](https://gnews.io) (free: 100 req/day) |
| `TELEGRAM_BOT_TOKEN` | Optional | For trade notifications |
| `TELEGRAM_CHAT_ID` | Optional | For trade notifications |
| `POLYMARKET_LIVE_ENABLED` | For live trading | Set to `true` to enable |
| `POLYMARKET_PROXY_URL` | If geo-blocked | `socks5://user:pass@host:1080` |

**Minimum to get started:** Just the Polymarket API keys. The bot works without LLM keys (falls back to keyword heuristics) and without news API keys (uses free RSS feeds from BBC, NYT, Al Jazeera, etc.).

**API key derivation:** If you have a wallet private key but no API credentials yet:
```bash
python scripts/setup_api_creds.py
```
This derives your API key, secret, and passphrase from your private key and writes them to `.env`.

### 3. Run it

```bash
# Scan markets for opportunities
python -m src.main scan

# Start paper trading (simulated, no real money)
python -m src.main paper

# See your results
python -m src.main report
```

That's it. You're running.

---

## All Commands

| Command | What it does |
|---------|-------------|
| `python -m src.main scan` | Scan live markets for trade signals |
| `python -m src.main scan -v` | Verbose scan (shows why each market was skipped) |
| `python -m src.main paper` | Run one round of paper trading |
| `python -m src.main live` | Run one round of live trading (real USDC) |
| `python -m src.main fast` | Fast-cycle short-term trading (crypto + tweet brackets) |
| `python -m src.main backtest` | Backtest strategy on collected data |
| `python -m src.main collect` | Save market snapshots (run regularly to build history) |
| `python -m src.main resolve` | Fetch resolved markets for accuracy analysis |
| `python -m src.main calibrate` | Analyze prediction accuracy vs outcomes |
| `python -m src.main edge` | Monte Carlo analysis across all strategies |
| `python -m src.main status` | Show system status and API connectivity |
| `python -m src.main report` | Full performance report with news impact |
| `python -m src.main stats` | Trade journal accuracy and calibration report |

**Switch strategies** with the `-s` flag:

```bash
python -m src.main scan -s value_betting
python -m src.main scan -s momentum
python -m src.main scan -s arbitrage
python -m src.main scan -s news_enhanced    # default
```

---

## Strategies

| Strategy | How it works |
|----------|-------------|
| **value_betting** | Estimates "true" probability, bets when market price diverges |
| **momentum** | Detects sustained price trends with volume confirmation |
| **arbitrage** | Finds YES+NO pricing inefficiencies (spread > fees) |
| **news_enhanced** | Blends statistical edge with LLM news analysis (default) |
| **short_term** | Fast-cycle: 15-min/1-hour crypto markets + tweet brackets |

### News Intelligence

When an LLM API key is set, the bot:
1. Pulls news from NewsAPI, GNews, and 10+ RSS feeds
2. Matches articles to active Polymarket markets
3. Asks the LLM how the news shifts probability
4. Blends the LLM estimate (40%) with the statistical estimate (60%)
5. Boosts or penalizes position sizes based on agreement

Without an LLM key it falls back to keyword-based heuristics (still works, just less accurate).

### Short-Term Trading (`fast` command)

For frequent traders. Designed to run every minute via cron:
- **Crypto momentum**: Trades SOL 15-min and 1-hour up/down markets using Binance technical analysis (RSI, VWAP, support/resistance, volume)
- **Tweet brackets**: Trades Elon Musk tweet count brackets with probability modeling
- Automatic profit-taking when positions move in your favor
- Resolution tracking via CLOB orderbook and time-based settlement

---

## Risk Management

The bot enforces strict limits:

| Limit | Default | Config key |
|-------|---------|-----------|
| Daily loss cap | $40 (20% of balance) | `risk.max_daily_loss_usdc` |
| Max drawdown kill switch | 20% | `risk.max_drawdown_pct` |
| Circuit breaker | 3 consecutive losses | `risk.circuit_breaker_losses` |
| Cooldown after circuit breaker | 2 hours | `risk.cooldown_minutes` |
| Position size range | $2 - $50 | `trading.min/max_position_usdc` |
| Max open positions | 50 | `trading.max_open_positions` |
| Probability bounds | 0.15 - 0.85 | `risk.min/max_entry_probability` |

All configurable in `config/default.yaml`.

---

## Live Trading

Live trading places real CLOB orders with real USDC. To enable:

1. Fund a Polygon wallet with USDC
2. Add your private key to `.env`: `POLYMARKET_PRIVATE_KEY=0x...`
3. Run API credential setup: `python scripts/setup_api_creds.py`
4. Set `POLYMARKET_LIVE_ENABLED=true` in `.env`
5. If geo-blocked, set `POLYMARKET_PROXY_URL` (SOCKS5 or HTTP proxy)

```bash
# Single live trading round
python -m src.main live

# Fast-cycle (run via cron every minute)
python -m src.main fast
```

The bot has three safety gates before it will execute real trades:
1. Config mode must be `live`
2. `POLYMARKET_LIVE_ENABLED=true` must be set
3. All API credentials must be valid

---

## Automate It (Optional)

### Long-term strategies (every 4 hours)

```bash
crontab -e
```
```
0 */4 * * * /path/to/trading-bot/cron_run.sh >> /path/to/trading-bot/logs/cron.log 2>&1
```

### Short-term fast-cycle (every minute)

```
* * * * * /path/to/trading-bot/cron_fast.sh >> /path/to/trading-bot/logs/fast.log 2>&1
```

---

## Project Structure

```
trading-bot/
├── src/
│   ├── main.py                    # CLI entry point (all commands)
│   ├── config.py                  # YAML config loader
│   ├── exchange/
│   │   ├── polymarket_client.py   # Polymarket Gamma + CLOB API client
│   │   └── proxy.py              # SOCKS5/HTTP proxy for geo-blocking
│   ├── data/
│   │   ├── feed.py               # Market data & snapshot persistence
│   │   ├── indicators.py         # Price/volume/edge indicators
│   │   ├── news_feed.py          # News aggregation (APIs + RSS)
│   │   ├── event_markets.py      # Short-term market discovery (crypto, tweets)
│   │   ├── binance_client.py     # Binance API (RSI, VWAP, candles)
│   │   ├── crypto_prices.py      # Real-time crypto price feeds
│   │   ├── crypto_model.py       # Crypto probability modeling
│   │   ├── categorizer.py        # Market categorization
│   │   ├── journal.py            # Trade journal (JSONL, per-day)
│   │   └── memory.py             # Persistent bot memory
│   ├── intelligence/
│   │   └── analyzer.py           # LLM-powered news analysis (Claude/OpenAI)
│   ├── strategy/
│   │   ├── base.py               # Strategy interface
│   │   ├── value_betting.py      # Value betting
│   │   ├── momentum.py           # Momentum
│   │   ├── arbitrage.py          # Arbitrage
│   │   ├── news_enhanced.py      # News + stats blend
│   │   └── short_term.py         # Crypto momentum + tweet brackets
│   ├── risk/
│   │   └── manager.py            # Risk limits, circuit breaker, kill switch
│   ├── engine/
│   │   ├── backtest.py           # Backtesting engine
│   │   ├── paper.py              # Paper trading engine
│   │   ├── live.py               # Live CLOB trading engine
│   │   ├── collector.py          # Resolved market collector
│   │   ├── edge_analyzer.py      # Monte Carlo edge analysis
│   │   └── report.py             # Performance reporting
│   ├── notifications/
│   │   └── telegram.py           # Telegram trade alerts
│   └── utils/
│       └── logger.py             # Logging setup
├── config/
│   └── default.yaml              # All bot settings
├── scripts/
│   ├── setup_api_creds.py        # Derive API keys from private key
│   ├── generate_test_data.py     # Generate synthetic test data
│   ├── monitor.py                # Performance monitoring
│   └── ensure_tunnel.sh          # Proxy tunnel helper
├── tests/                        # Unit tests (50 tests)
├── data/                         # Snapshots, sessions, journals (auto-created)
├── logs/                         # Log files (auto-created)
├── .env.example                  # API key template
├── cron_run.sh                   # Cron: long-term trading (4h)
├── cron_fast.sh                  # Cron: short-term trading (1m)
└── requirements.txt              # Python dependencies
```

---

## Configuration

All settings are in `config/default.yaml`. Key things to change:

```yaml
trading:
  mode: paper                    # paper or live
  default_position_usdc: 10.0   # How much per trade
  max_position_usdc: 50.0       # Max single trade size

risk:
  max_daily_loss_usdc: 40.0     # Stop after this daily loss
  max_drawdown_pct: 20.0        # Kill switch threshold

strategy:
  active: news_enhanced          # Which strategy to use

backtest:
  starting_balance_usdc: 200.0  # Paper trading starting balance
```

---

## FAQ

**Q: Will this trade real money by default?**
A: No. It starts in paper mode. Live trading requires you to explicitly set `POLYMARKET_LIVE_ENABLED=true`, provide a funded wallet, and have valid API credentials. Three safety gates must pass.

**Q: Do I need all the API keys?**
A: No. Just the Polymarket keys to scan markets. LLM and news keys make the `news_enhanced` strategy smarter, but it works without them.

**Q: I'm getting 403 errors on live trades.**
A: Polymarket blocks order placement from certain countries. Set `POLYMARKET_PROXY_URL` in your `.env` to a SOCKS5 or HTTP proxy.

**Q: How do I see my results?**
A: `python -m src.main report` for performance overview. `python -m src.main stats` for detailed trade journal with accuracy calibration.

**Q: Can I add my own strategy?**
A: Yes. Create a file in `src/strategy/`, extend `BaseStrategy`, implement `evaluate()`, and register it in `src/strategy/__init__.py`.

---

## Disclaimer

**This software is for educational and research purposes only.** Trading prediction markets involves financial risk. This bot does not guarantee profits. Use at your own risk. The authors are not responsible for any financial losses. Always do your own research before risking real capital.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
