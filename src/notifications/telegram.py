"""Telegram notification module for the Polymarket trading bot.

Sends trade alerts, daily summaries, and error notifications via the
Telegram Bot API.  Uses only ``urllib`` from the standard library so no
extra dependencies are required.

Environment variables (stored in .env):
    TELEGRAM_BOT_TOKEN  - Bot token from @BotFather
    TELEGRAM_CHAT_ID    - Target chat / user ID (use ``get_chat_id()`` to discover)
"""

import json
import logging
import os
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("trading_bot.telegram")

_BASE_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    """Thin wrapper around the Telegram Bot API for trade notifications."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

        if not self.bot_token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN not set - Telegram notifications disabled"
            )

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _api_url(self, method: str) -> str:
        """Build the full Telegram API URL for *method*."""
        return _BASE_URL.format(token=self.bot_token, method=method)

    def _call(self, method: str, payload: dict[str, Any]) -> dict | None:
        """Make a POST request to the Telegram Bot API.

        Returns the parsed JSON response on success, or ``None`` on any
        error.  Errors are logged but never raised so the trading bot is
        never interrupted by a notification failure.
        """
        if not self.bot_token:
            logger.debug("Telegram call skipped - no bot token configured")
            return None

        url = self._api_url(method)
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if not body.get("ok"):
                    logger.error("Telegram API error: %s", body)
                    return None
                return body
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = str(exc)
            logger.error("Telegram HTTP %s: %s", exc.code, err_body)
        except urllib.error.URLError as exc:
            logger.error("Telegram URL error: %s", exc.reason)
        except Exception as exc:
            logger.error("Telegram unexpected error: %s", exc)

        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_message(self, text: str, parse_mode: str = "HTML") -> dict | None:
        """Send a plain text message to the configured ``chat_id``.

        Returns the Telegram API response dict on success, ``None`` on
        failure.  Failures are logged but never raised.
        """
        if not self.chat_id:
            logger.warning("Cannot send message - TELEGRAM_CHAT_ID not set")
            return None

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        return self._call("sendMessage", payload)

    def send_trade_alert(self, signal_data: dict) -> dict | None:
        """Send a nicely formatted trade alert.

        ``signal_data`` is expected to have keys matching a
        ``TradeSignal`` or a dict produced by ``dataclasses.asdict()``
        on a trade position.  Recognised keys:

        * signal - ``"BUY_YES"`` or ``"BUY_NO"``
        * question - market question text
        * market_id
        * entry_price
        * position_size_usdc
        * edge
        * confidence
        * reason
        * shares (optional)
        * trade_id (optional)
        """
        signal = signal_data.get("signal", "UNKNOWN")
        is_buy_yes = "YES" in str(signal).upper()

        # Direction emoji: green for YES, red for NO
        direction_emoji = "\U0001f7e2" if is_buy_yes else "\U0001f534"  # green / red circle

        question = signal_data.get("question", "Unknown market")
        entry_price = signal_data.get("entry_price", 0)
        size = signal_data.get("position_size_usdc", 0)
        edge = signal_data.get("edge", 0)
        confidence = signal_data.get("confidence", 0)
        reason = signal_data.get("reason", "")
        shares = signal_data.get("shares", 0)
        trade_id = signal_data.get("trade_id", "")

        q_short = _escape_html(question[:50])
        lines = [
            f"{direction_emoji} <b>{signal}</b> ${size:.2f} @ {entry_price:.3f}",
            f"{q_short}",
            f"Edge: {edge:+.3f} | {shares:.1f} shares",
        ]

        return self.send_message("\n".join(lines))

    def send_daily_summary(self, session_summary: dict) -> dict | None:
        """Send a daily P&L / session summary.

        ``session_summary`` should match the dict returned by
        ``PaperEngine.get_summary()`` or ``LiveEngine.get_summary()``.
        """
        pnl = session_summary.get("total_pnl", 0)
        pnl_emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"  # chart up / chart down

        balance = session_summary.get("current_balance", 0)
        starting = session_summary.get("starting_balance", 0)
        total_trades = session_summary.get("total_trades", 0)
        wins = session_summary.get("wins", 0)
        losses = session_summary.get("losses", 0)
        win_rate = session_summary.get("win_rate", "0.0%")
        open_positions = session_summary.get("open_positions", 0)
        strategy = session_summary.get("strategy", "unknown")
        session_id = session_summary.get("session_id", "")

        # P&L colour indicator
        pnl_sign = "+" if pnl >= 0 else ""

        lines = [
            f"{pnl_emoji} <b>Daily: {pnl_sign}${pnl:.2f}</b>",
            f"Bal: ${balance:.2f} | W:{wins} L:{losses} | {win_rate}",
            f"Open: {open_positions} | Trades: {total_trades}",
        ]

        return self.send_message("\n".join(lines))

    def send_error(self, error_msg: str) -> dict | None:
        """Send an error notification.

        The message is prefixed with a warning emoji so it stands out
        in the chat.
        """
        text = f"\U000026A0\U0000FE0F <b>Error:</b> <code>{_escape_html(str(error_msg)[:100])}</code>"
        return self.send_message(text)

    def get_chat_id(self) -> str | None:
        """Discover the chat_id by reading the most recent ``/start``
        message from ``getUpdates``.

        Workflow:
        1. The user sends ``/start`` to the bot in Telegram.
        2. Call this method.  It reads the latest update and extracts
           the ``chat.id``.
        3. Store the returned value as ``TELEGRAM_CHAT_ID`` in ``.env``.

        Returns the chat_id string, or ``None`` if no messages are
        available.
        """
        result = self._call("getUpdates", {"limit": 10, "timeout": 0})
        if not result:
            return None

        updates = result.get("result", [])
        if not updates:
            logger.info(
                "No updates found.  Send /start to @%s in Telegram first.",
                self._get_bot_username(),
            )
            return None

        # Walk updates in reverse to find the most recent /start (or any message)
        for update in reversed(updates):
            msg = update.get("message", {})
            chat = msg.get("chat", {})
            chat_id = chat.get("id")
            if chat_id is not None:
                chat_id_str = str(chat_id)
                logger.info("Discovered Telegram chat_id: %s", chat_id_str)
                self.chat_id = chat_id_str
                return chat_id_str

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_bot_username(self) -> str:
        """Fetch the bot's username via ``getMe``."""
        result = self._call("getMe", {})
        if result:
            return result.get("result", {}).get("username", "unknown_bot")
        return "unknown_bot"

    def is_configured(self) -> bool:
        """Return ``True`` if both token and chat_id are set."""
        return bool(self.bot_token and self.chat_id)


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram's HTML parse mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
