"""Small, explicit trading-day checks for scheduled market tasks."""

from __future__ import annotations

from datetime import datetime

from config import settings


def is_cn_a_share_trading_day(moment: datetime) -> bool:
    """Return whether the configured A-share market is open that calendar day."""
    local = moment.astimezone(settings.SHA_TZ)
    return (
        local.weekday() < 5
        and local.date().isoformat() not in settings.CN_MARKET_HOLIDAYS
    )


def is_us_equity_trading_day(moment: datetime) -> bool:
    """Return whether the configured US equity market is open that calendar day."""
    local = moment.astimezone(settings.US_EASTERN_TZ)
    return (
        local.weekday() < 5
        and local.date().isoformat() not in settings.US_MARKET_HOLIDAYS
    )
