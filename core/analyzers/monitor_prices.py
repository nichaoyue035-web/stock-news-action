"""Short-interval watchlist price monitoring."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Callable, Optional

from config import settings
from core.market_calendar import is_cn_a_share_trading_day
from core.monitor_store import MonitorStore
from utils.notifier import log_info


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _normalise_watchlist_codes(raw_codes: list[str]) -> list[str]:
    """Accept six-digit A-share codes only and preserve their configured order."""
    codes: list[str] = []
    for raw_code in raw_codes:
        code = str(raw_code or "").strip()
        if not code.isdigit() or len(code) > 6:
            log_info(f"忽略无效 WATCHLIST_CODES 条目: {code or '空'}")
            continue
        code = code.zfill(6)
        if code not in codes:
            codes.append(code)
    return codes


def _is_a_share_trading_session(now: datetime) -> bool:
    """Avoid treating closed-market snapshots as fresh short-interval changes."""
    if not is_cn_a_share_trading_day(now):
        return False
    current_time = now.time().replace(tzinfo=None)
    return (
        time(9, 30) <= current_time <= time(11, 30)
        or time(13, 0) <= current_time <= time(15, 0)
    )


def _build_price_alert(
    *,
    code: str,
    name: str,
    previous: dict[str, Any],
    current_price: float,
    day_pct: Optional[float],
    change_pct: float,
    now: datetime,
) -> str:
    from core.formatter import _format_market_message

    direction = "快速上涨" if change_pct > 0 else "快速下跌"
    day_pct_text = f"{day_pct:+.2f}%" if day_pct is not None else "未知"
    return _format_market_message(
        "自选股分钟异动",
        report_time=now.strftime("%Y-%m-%d %H:%M"),
        source="东方财富实时行情",
        category="行情",
        importance="高",
        summary=(
            f"{name} ({code}) {direction}："
            f"{float(previous['price']):.2f} → {current_price:.2f}，"
            f"区间变动 {change_pct:+.2f}%；当日涨跌 {day_pct_text}。"
        ),
        impact=(
            f"触发 {settings.PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES} 分钟内 "
            f"{settings.PRICE_ALERT_MINUTE_CHANGE_PCT:.2f}% 的价格异动阈值。"
            "这只是行情变化提示，不构成交易建议。"
        ),
        links="未知",
        market_scope="个股",
        related_sectors=[name],
    )



def run_watchlist_monitor(
    store: MonitorStore,
    now: datetime,
    *,
    get_stock_quote: Callable[[str], Optional[dict[str, Any]]],
    claim_and_send: Callable[..., bool],
) -> tuple[int, int]:
    """Store minute quotes and send rate-limited alerts for large short-term moves."""
    codes = _normalise_watchlist_codes(settings.WATCHLIST_CODES)
    if not codes:
        log_info("行情监控跳过：未配置 WATCHLIST_CODES")
        return 0, 0
    if not _is_a_share_trading_session(now):
        log_info("行情监控跳过：当前不在 A 股常规交易时段")
        return 0, 0

    quote_count = 0
    signal_count = 0
    for code in codes:
        quote = get_stock_quote(code)
        if not quote:
            continue
        price = _safe_float(quote.get("price"))
        day_pct = _safe_float(quote.get("pct"))
        if price is None or price <= 0:
            log_info(f"行情监控跳过无效价格: {code}")
            continue

        quote_count += 1
        name = str(quote.get("name") or code)
        previous = store.record_quote(
            code=code,
            name=name,
            price=price,
            pct=day_pct,
            observed_at=now,
            max_gap_minutes=settings.PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES,
        )
        if not previous or float(previous["price"]) <= 0:
            continue

        change_pct = (price / float(previous["price"]) - 1) * 100
        if abs(change_pct) < settings.PRICE_ALERT_MINUTE_CHANGE_PCT:
            continue

        direction = "up" if change_pct > 0 else "down"
        alert_key = f"price:{code}:{now.strftime('%Y%m%d%H%M')}:{direction}"
        if claim_and_send(
            store,
            alert_key=alert_key,
            dedup_key=f"price:{code}:{direction}",
            alert_type="price_move",
            severity="high",
            content=_build_price_alert(
                code=code,
                name=name,
                previous=previous,
                current_price=price,
                day_pct=day_pct,
                change_pct=change_pct,
                now=now,
            ),
            payload={
                "code": code,
                "name": name,
                "previous": previous,
                "current_price": price,
                "day_pct": day_pct,
                "change_pct": change_pct,
            },
            now=now,
            cooldown_minutes=settings.PRICE_ALERT_COOLDOWN_MINUTES,
        ):
            signal_count += 1

    return quote_count, signal_count
