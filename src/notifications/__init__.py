"""Notification subsystem for the Polymarket trading bot."""

from .telegram import TelegramNotifier

__all__ = ["TelegramNotifier"]
