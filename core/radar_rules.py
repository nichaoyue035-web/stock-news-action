"""Deterministic market-session and value rules for the candidate radar."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Optional

from config import settings
from core.market_calendar import is_cn_a_share_trading_day, is_us_equity_trading_day
from utils.notifier import log_info


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def is_experimental_yahoo_source(attributes: dict[str, Any]) -> bool:
    return str(attributes.get("source") or "").startswith("yfinance-")


def normalise_a_share_codes(raw_codes: list[str]) -> list[str]:
    codes: list[str] = []
    for raw_code in raw_codes:
        code = str(raw_code or "").strip()
        if not code.isdigit() or len(code) > 6:
            log_info(f"雷达忽略无效 A 股代码: {code or '空'}")
            continue
        code = code.zfill(6)
        if code not in codes:
            codes.append(code)
    return codes


def is_a_share_trading_session(now: datetime) -> bool:
    if not is_cn_a_share_trading_day(now):
        return False
    current_time = now.astimezone(settings.SHA_TZ).time().replace(tzinfo=None)
    return time(9, 30) <= current_time <= time(11, 30) or time(13, 0) <= current_time <= time(15, 0)


def is_us_trading_session(now: datetime) -> bool:
    """Cover US pre-market, regular session and after-hours in Eastern Time."""
    local = now.astimezone(settings.US_EASTERN_TZ)
    if not is_us_equity_trading_day(local):
        return False
    current_time = local.time().replace(tzinfo=None)
    return time(4, 0) <= current_time <= time(20, 0)


def market_label(market: str) -> str:
    return "A股" if market == "CN" else "美股"


def market_close_minutes(market: str, now: datetime) -> int:
    timezone = settings.SHA_TZ if market == "CN" else settings.US_EASTERN_TZ
    local_now = now.astimezone(timezone)
    close_time = time(15, 0) if market == "CN" else time(16, 0)
    close_at = local_now.replace(
        hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0
    )
    if close_at <= local_now:
        return 1
    return max(1, int((close_at - local_now).total_seconds() // 60))


def market_session_start(market: str, now: datetime) -> datetime:
    timezone = settings.SHA_TZ if market == "CN" else settings.US_EASTERN_TZ
    local_now = now.astimezone(timezone)
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0)
