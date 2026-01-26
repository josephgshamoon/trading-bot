# 🤖 AI Trading Bot - Polymarket Prediction Markets

## 🎯 Goal
Build an AI-powered trading system for Polymarket prediction markets with:
- Strict risk management
- Data-driven strategy
- Paper trading before live deployment
- Full audit trail

## ⚠️ Safety Rules
This bot operates under STRICT safety rules:
- NO auto-trading until Phase 3 (explicit approval required)
- Max $1 per trade initially
- Kill switch always active
- 20% max drawdown before stopping

## 📊 Project Phases

### Phase 1: Data Collection & Backtesting (NOW)
- [x] Repo structure
- [x] Polymarket API client
- [x] Data collector (runs hourly)
- [x] Strategy configuration
- [ ] Historical database building
- [ ] Backtesting on real data

### Phase 2: Paper Trading (Next)
- [ ] Hourly market scanning
- [ ] Telegram signals for approval
- [ ] Track real performance
- [ ] 2+ weeks paper trading

### Phase 3: Live Trading (Later)
- [ ] Your explicit approval
- [ ] Kill switch ready
- [ ] Full risk controls

## 🚀 Quick Start

```bash
# Run data collector once
python3 src/data_collector.py --once

# Run continuously (hourly)
python3 src/data_collector.py --continuous

# Run paper trader
python3 src/paper_trader.py --scan
```

## 📁 Project Structure

```
trading-bot/
├── src/
│   ├── polymarket_client.py   # API client
│   ├── data_collector.py     # Hourly data collection
│   ├── backtest.py          # Backtesting engine
│   ├── paper_trader.py      # Paper trading runner
│   └── bot.py               # Main orchestrator
├── config/
│   ├── config.yaml          # Bot configuration
│   └── strategy.yaml        # Strategy rules
├── data/
│   └── market_history.csv   # Historical data
├── logs/                    # All logs
└── tests/                   # Tests
```

## 📈 Strategy (Defined in config/strategy.yaml)

| Parameter | Value |
|-----------|-------|
| Probability Range | 10% - 90% |
| Position Size | $1.00 |
| Min Volume | $50,000 |
| Max Trades/Day | 2 |
| Max Daily Loss | 20% |

## 🛡️ Risk Management

- Kill switch: Always active
- Max daily loss: 20%
- Max drawdown: 20%
- Max trades/day: 2
- Position size: Fixed $1

## 📊 Cron Jobs (Automatic)

```bash
# Data collector - every hour
0 * * * * python3 src/data_collector.py --once

# Paper trader - every hour
5 * * * * python3 src/paper_trader.py --scan
```

## 🔗 Links

- **GitHub:** https://github.com/joeyquack/trading-bot
- **Notion:** PA Bot Notes (connected)

## 📝 License

MIT License - Trade responsibly!

---

*Built with ❤️ by PA Bot*
