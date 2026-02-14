"""Risk management system — position limits, drawdown protection, circuit breakers."""

import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger("trading_bot.risk")


@dataclass
class DailyStats:
    """Track daily trading statistics."""
    date: str = ""
    trades_today: int = 0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: datetime | None = None

    def reset(self, date_str: str):
        self.date = date_str
        self.trades_today = 0
        self.daily_pnl = 0.0
        # Don't reset consecutive_losses or cooldown — those persist


@dataclass
class Portfolio:
    """Track overall portfolio state."""
    balance: float = 0.0
    starting_balance: float = 0.0
    peak_balance: float = 0.0
    open_positions: list = field(default_factory=list)
    trade_history: list = field(default_factory=list)

    @property
    def total_value(self) -> float:
        """Total portfolio value = cash + invested capital in open positions."""
        invested = sum(p.get("size_usdc", 0) for p in self.open_positions)
        return self.balance + invested

    @property
    def drawdown(self) -> float:
        """Drawdown based on total portfolio value, not just cash.

        Invested capital in open positions is not a loss — only realized
        losses and actual value decline count toward drawdown.
        """
        if self.peak_balance <= 0:
            return 0.0
        return (self.peak_balance - self.total_value) / self.peak_balance * 100

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)


class RiskManager:
    """Enforces risk limits and position sizing rules."""

    def __init__(self, config: dict):
        self.config = config
        risk_cfg = config.get("risk", {})
        trading_cfg = config.get("trading", {})

        self.max_daily_loss = risk_cfg.get("max_daily_loss_usdc", 20.0)
        self.max_drawdown_pct = risk_cfg.get("max_drawdown_pct", 20.0)
        self.max_trades_per_day = risk_cfg.get("max_trades_per_day", 10)
        self.circuit_breaker_losses = risk_cfg.get("circuit_breaker_losses", 3)
        self.cooldown_minutes = risk_cfg.get("cooldown_minutes", 120)
        self.max_open_positions = trading_cfg.get("max_open_positions", 5)
        self.max_position_usdc = trading_cfg.get("max_position_usdc", 25.0)
        self.min_position_usdc = trading_cfg.get("min_position_usdc", 1.0)

        self.daily = DailyStats()
        self.portfolio = Portfolio()
        self._kill_switch = False

        logger.info(
            f"RiskManager initialized: max_daily_loss=${self.max_daily_loss}, "
            f"max_drawdown={self.max_drawdown_pct}%, "
            f"max_trades/day={self.max_trades_per_day}"
        )

    def initialize_portfolio(self, balance: float):
        """Set initial portfolio balance."""
        self.portfolio.balance = balance
        self.portfolio.starting_balance = balance
        self.portfolio.peak_balance = balance
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily.reset(today)

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is currently allowed.

        Returns (allowed, reason) tuple.
        """
        if self._kill_switch:
            return False, "Kill switch is active"

        # Check daily date rollover
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily.date != today:
            self.daily.reset(today)

        # Cooldown check
        if self.daily.cooldown_until:
            now = datetime.now(timezone.utc)
            if now < self.daily.cooldown_until:
                remaining = (self.daily.cooldown_until - now).seconds // 60
                return False, f"Cooldown active — {remaining} minutes remaining"
            else:
                self.daily.cooldown_until = None
                self.daily.consecutive_losses = 0
                logger.info("Cooldown period ended, trading resumed")

        # Daily trade limit
        if self.daily.trades_today >= self.max_trades_per_day:
            return False, f"Daily trade limit reached ({self.max_trades_per_day})"

        # Daily loss limit
        if abs(self.daily.daily_pnl) >= self.max_daily_loss and self.daily.daily_pnl < 0:
            return False, f"Daily loss limit reached (${self.daily.daily_pnl:.2f})"

        # Drawdown limit
        if self.portfolio.drawdown >= self.max_drawdown_pct:
            return False, f"Max drawdown reached ({self.portfolio.drawdown:.1f}%)"

        # Open position limit
        if self.portfolio.open_position_count >= self.max_open_positions:
            return False, f"Max open positions reached ({self.max_open_positions})"

        # Balance check
        if self.portfolio.balance < self.min_position_usdc:
            return False, f"Insufficient balance (${self.portfolio.balance:.2f})"

        return True, "OK"

    def validate_trade(self, signal) -> tuple[bool, str]:
        """Validate a specific trade signal against risk rules.

        Args:
            signal: TradeSignal from a strategy.

        Returns:
            (allowed, reason) tuple.
        """
        allowed, reason = self.can_trade()
        if not allowed:
            return False, reason

        # Position size limits
        if signal.position_size_usdc > self.max_position_usdc:
            return False, f"Position too large (${signal.position_size_usdc} > ${self.max_position_usdc})"

        if signal.position_size_usdc < self.min_position_usdc:
            return False, f"Position too small (${signal.position_size_usdc} < ${self.min_position_usdc})"

        if signal.position_size_usdc > self.portfolio.balance:
            return False, f"Insufficient balance for ${signal.position_size_usdc} trade"

        # Don't double-up on the same market (unless scaling is allowed)
        is_crypto = getattr(signal, "metadata", {}).get("strategy") in ("crypto_momentum_15m", "crypto_momentum_1h")
        if not is_crypto:
            for pos in self.portfolio.open_positions:
                if pos.get("market_id") == signal.market_id:
                    return False, f"Already have position in market {signal.market_id}"

        return True, "OK"

    def record_trade_entry(self, signal, trade_id: str = ""):
        """Record a new trade entry."""
        self.daily.trades_today += 1
        self.portfolio.balance -= signal.position_size_usdc

        position = {
            "trade_id": trade_id,
            "market_id": signal.market_id,
            "question": signal.question,
            "signal": signal.signal.value,
            "entry_price": signal.entry_price,
            "size_usdc": signal.position_size_usdc,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.portfolio.open_positions.append(position)

        logger.info(
            f"Trade entered: {signal.signal.value} ${signal.position_size_usdc:.2f} "
            f"on {signal.question[:50]}..."
        )

    def record_trade_exit(self, identifier: str, pnl: float):
        """Record a trade exit and update stats.

        Args:
            identifier: trade_id or market_id of the position to close.
            pnl: realized profit/loss.
        """
        # Remove from open positions — match trade_id first, fallback to market_id
        before_count = len(self.portfolio.open_positions)
        self.portfolio.open_positions = [
            p for p in self.portfolio.open_positions
            if p.get("trade_id") != identifier and p.get("market_id") != identifier
        ]
        removed = before_count - len(self.portfolio.open_positions)
        if removed > 1:
            logger.warning(
                f"record_trade_exit removed {removed} positions for {identifier} "
                f"(expected 1) — possible duplicate"
            )

        self.daily.daily_pnl += pnl
        self.portfolio.balance += pnl

        # Update peak balance based on total portfolio value
        self.portfolio.peak_balance = max(
            self.portfolio.peak_balance, self.portfolio.total_value
        )

        # Track consecutive losses
        if pnl < 0:
            self.daily.consecutive_losses += 1
            if self.daily.consecutive_losses >= self.circuit_breaker_losses:
                self.daily.cooldown_until = datetime.now(timezone.utc) + timedelta(
                    minutes=self.cooldown_minutes
                )
                logger.warning(
                    f"Circuit breaker triggered after {self.daily.consecutive_losses} "
                    f"consecutive losses. Cooldown for {self.cooldown_minutes} minutes."
                )
        else:
            self.daily.consecutive_losses = 0

        self.portfolio.trade_history.append({
            "identifier": identifier,
            "pnl": pnl,
            "balance_after": self.portfolio.balance,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Trade exited: pnl=${pnl:+.2f}, balance=${self.portfolio.balance:.2f}, "
            f"drawdown={self.portfolio.drawdown:.1f}%"
        )

    def activate_kill_switch(self):
        """Emergency stop all trading."""
        self._kill_switch = True
        logger.critical("KILL SWITCH ACTIVATED — all trading halted")

    def deactivate_kill_switch(self):
        """Re-enable trading after kill switch."""
        self._kill_switch = False
        logger.warning("Kill switch deactivated — trading re-enabled")

    def get_status(self) -> dict:
        """Return current risk status summary."""
        can_trade, reason = self.can_trade()
        return {
            "can_trade": can_trade,
            "reason": reason,
            "kill_switch": self._kill_switch,
            "balance": self.portfolio.balance,
            "total_value": self.portfolio.total_value,
            "peak_balance": self.portfolio.peak_balance,
            "drawdown_pct": round(self.portfolio.drawdown, 2),
            "daily_pnl": round(self.daily.daily_pnl, 2),
            "trades_today": self.daily.trades_today,
            "consecutive_losses": self.daily.consecutive_losses,
            "open_positions": self.portfolio.open_position_count,
            "cooldown_active": self.daily.cooldown_until is not None,
        }
