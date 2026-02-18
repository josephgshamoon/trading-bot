# Polymarket Trading Bot

Automated trading bot for [Polymarket](https://polymarket.com) prediction markets. Uses news intelligence (LLM-powered) and statistical strategies to find edges in YES/NO markets.

**Current mode: Paper trading** (simulated trades, no real money). Live trading is not yet implemented.

## What It Does

- Scans Polymarket for mispriced prediction markets
- Analyzes news via Claude/OpenAI to detect probability shifts
- Runs 4 strategies: value betting, momentum, arbitrage, and news-enhanced
- Paper trades with full position tracking and risk management
- Backtests strategies with Monte Carlo simulation

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

Open `.env` in any text editor and fill in your keys:

| Key | Required? | Where to get it |
|-----|-----------|-----------------|
| `POLYMARKET_API_KEY` | Yes | [docs.polymarket.com](https://docs.polymarket.com) |
| `POLYMARKET_API_SECRET` | Yes | Same as above |
| `POLYMARKET_PASSPHRASE` | Yes | Same as above |
| `ANTHROPIC_API_KEY` | Recommended | [console.anthropic.com](https://console.anthropic.com) |
| `OPENAI_API_KEY` | Optional | [platform.openai.com](https://platform.openai.com) (fallback LLM) |
| `NEWSAPI_KEY` | Optional | [newsapi.org](https://newsapi.org) (free: 100 req/day) |
| `GNEWS_API_KEY` | Optional | [gnews.io](https://gnews.io) (free: 100 req/day) |
| `TELEGRAM_BOT_TOKEN` | Optional | For trade notifications |
| `TELEGRAM_CHAT_ID` | Optional | For trade notifications |

**Minimum to get started:** Just the Polymarket keys. The bot works without LLM keys (falls back to keyword heuristics) and without news API keys (uses free RSS feeds).

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
| `python -m src.main backtest` | Backtest strategy on collected data |
| `python -m src.main collect` | Save market snapshots (run regularly to build data) |
| `python -m src.main resolve` | Fetch resolved markets for accuracy analysis |
| `python -m src.main calibrate` | Analyze how well the bot predicts outcomes |
| `python -m src.main edge` | Monte Carlo analysis across all strategies |
| `python -m src.main status` | Show system status and connectivity |
| `python -m src.main report` | Full performance report with news impact |

**Switch strategies** with the `-s` flag:

```bash
python -m src.main scan -s value_betting
python -m src.main scan -s momentum
python -m src.main scan -s arbitrage
python -m src.main scan -s news_enhanced    # default
```

---

## Automate It (Optional)

Run the bot every 4 hours automatically:

```bash
crontab -e
```

Add this line (replace the path with your actual path):

```
0 */4 * * * /path/to/trading-bot/cron_run.sh >> /path/to/trading-bot/logs/cron.log 2>&1
```

---

## How It Works

### Strategies

| Strategy | How it finds trades |
|----------|-------------------|
| **value_betting** | Estimates "true" probability and bets when market price diverges |
| **momentum** | Detects sustained price trends with volume confirmation |
| **arbitrage** | Finds YES+NO pricing inefficiencies (spread > fees) |
| **news_enhanced** | Blends statistical edge with LLM news analysis (default) |

### Risk Management

The bot enforces strict limits to protect your balance:

- Daily loss cap (default: $40 / 20% of balance)
- Max drawdown kill switch (default: 20%)
- Circuit breaker after 3 consecutive losses (2-hour cooldown)
- Position size limits ($2 - $50 per trade)
- Max 50 open positions

All configurable in `config/default.yaml`.

### News Intelligence

When an LLM API key is set, the bot:

1. Pulls news from NewsAPI, GNews, and 10+ RSS feeds (BBC, NYT, Al Jazeera, etc.)
2. Matches articles to active Polymarket markets
3. Asks the LLM: "How does this news shift the probability?"
4. Blends the LLM's estimate (40%) with the statistical estimate (60%)
5. Boosts or penalizes position sizes based on agreement

Without an LLM key, it falls back to keyword-based heuristics (still works, just less accurate).

---

## Project Structure

```
trading-bot/
├── src/
│   ├── main.py                 # CLI entry point
│   ├── config.py               # Config loader
│   ├── exchange/
│   │   └── polymarket_client.py  # Polymarket API client
│   ├── data/
│   │   ├── feed.py             # Market data & snapshots
│   │   ├── indicators.py       # Price/volume indicators
│   │   └── news_feed.py        # News aggregation (APIs + RSS)
│   ├── intelligence/
│   │   └── analyzer.py         # LLM-powered news analysis
│   ├── strategy/
│   │   ├── base.py             # Strategy interface
│   │   ├── value_betting.py    # Value betting strategy
│   │   ├── momentum.py         # Momentum strategy
│   │   ├── arbitrage.py        # Arbitrage strategy
│   │   └── news_enhanced.py    # News + stats blend
│   ├── risk/
│   │   └── manager.py          # Risk management
│   ├── engine/
│   │   ├── backtest.py         # Backtesting engine
│   │   ├── paper.py            # Paper trading engine
│   │   ├── live.py             # Live trading (not yet implemented)
│   │   ├── collector.py        # Resolved market collector
│   │   ├── edge_analyzer.py    # Monte Carlo edge analysis
│   │   └── report.py           # Performance reporting
│   └── utils/
│       └── logger.py           # Logging setup
├── config/
│   └── default.yaml            # All bot settings
├── tests/                      # Unit tests
├── data/                       # Snapshots & results (auto-created)
├── logs/                       # Log files (auto-created)
├── .env.example                # API key template
├── cron_run.sh                 # Cron automation script
└── requirements.txt            # Python dependencies
```

---

## Configuration

All settings live in `config/default.yaml`. Key things you might want to change:

```yaml
trading:
  default_position_usdc: 10.0    # How much per trade
  max_position_usdc: 50.0        # Max single trade size

risk:
  max_daily_loss_usdc: 40.0      # Stop trading after this daily loss
  max_drawdown_pct: 20.0         # Kill switch at this drawdown

strategy:
  active: news_enhanced          # Which strategy to use

backtest:
  starting_balance_usdc: 200.0   # Paper trading starting balance
```

---

## FAQ

**Q: Will this trade real money?**
A: Not yet. Live trading (`engine/live.py`) is a placeholder. The bot currently only supports paper trading (simulated). You'd need to implement CLOB order placement to go live.

**Q: Do I need all the API keys?**
A: No. Just the Polymarket keys to scan markets. The LLM and news keys make the `news_enhanced` strategy smarter, but it works without them.

**Q: How do I see my paper trading results?**
A: Run `python -m src.main report` or check `data/paper_session.json`.

**Q: Can I add my own strategy?**
A: Yes. Create a new file in `src/strategy/`, extend `BaseStrategy`, implement the `evaluate()` method, and register it in `src/strategy/__init__.py`.

---

## Disclaimer

**This software is for educational and research purposes only.** Trading prediction markets involves financial risk. This bot does not guarantee profits. Use at your own risk. The authors are not responsible for any financial losses incurred from using this software. Always do your own research before risking real capital.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
