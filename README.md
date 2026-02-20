# Polymarket Trading Bot

A trading bot for Polymarket prediction markets. Browse markets, paper trade, backtest strategies, and (optionally) place real trades.

## Setup

```bash
git clone https://github.com/josephgshamoon/trading-bot.git
cd trading-bot
pip install -r requirements.txt
```

No `.env` file is needed to browse markets or paper trade.

## Commands

```bash
python3 run.py markets              # Browse top active markets
python3 run.py search "bitcoin"     # Search markets by keyword
python3 run.py market <ID>          # View market details + token IDs
python3 run.py paper <ID>           # Paper trade (simulated, no real money)
python3 run.py trade <ID>           # Real trade (requires wallet setup below)
python3 run.py backtest             # Run strategy backtest
python3 run.py collect              # Collect market data snapshot
```

## Real Trading (optional)

Real trading requires a Polygon wallet with USDC and the `py-clob-client` package.

1. Install the trading client:
   ```bash
   pip install py-clob-client
   ```

2. Create a `.env` file from the template:
   ```bash
   cp .env.example .env
   ```

3. Add your wallet private key to `.env`:
   ```
   POLYMARKET_PRIVATE_KEY=your_private_key_here
   ```

4. Run a trade (you will be asked to type "confirm" before any order is placed):
   ```bash
   python3 run.py trade <MARKET_ID>
   ```

## Telegram Notifications (optional)

To receive trade alerts on Telegram, add these to your `.env`:

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Get a bot token from @BotFather and your chat ID from @userinfobot.

## Project Structure

```
polymarket-bot/
  run.py                    # CLI entry point (start here)
  src/
    polymarket_client.py    # Polymarket API client
    trader.py               # Real trading (py-clob-client)
    paper_trader.py         # Paper trading
    backtest.py             # Backtesting engine
    data_collector.py       # Market data collection
    bot.py                  # Trading bot orchestrator
  config/
    config.yaml             # Bot configuration
    strategy.yaml           # Strategy parameters
```

## Configuration

Strategy parameters are in `config/config.yaml` under the `strategy:` section. Defaults:

| Parameter | Value |
|-----------|-------|
| Probability range | 10% - 90% |
| Position size | $1.00 USDC |
| Min volume | $50,000 |
| Max trades/day | 2 |
| Max daily loss | 20% |

## Safety

- Kill switch always active
- No auto-trading without explicit confirmation
- Paper trading by default
- All trades logged

## License

MIT
