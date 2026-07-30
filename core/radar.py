"""Orchestrate market radar scanning, delivery, lifecycle tracking and callbacks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from config import settings
from core.data_fetcher import (
    get_data_source_health,
    get_hot_stocks_data,
    get_stock_quote,
    get_us_stock_news,
    get_us_stock_quote,
    get_us_stock_snapshots,
)
from core.interaction_auth import is_authorized_interaction
from core import radar_messages, radar_rules, radar_scanners, radar_tracking
from core.radar_store import RadarStore
from core.runtime import (
    _record_fetch_success,
    _record_quality_counts,
    _set_run_reason,
    _set_run_summary,
    _send_tg_with_summary,
)
from core.yfinance_dev import (
    fetch_yfinance_broad_market_candidates,
    fetch_yfinance_dev_quotes,
)
from utils.notifier import log_error, log_info, send_tg_interactive


# Compatibility exports keep existing callers and automation tests stable while
# the implementation is divided by rules, messages, scanners and tracking.
RADAR_CALLBACK_PREFIX = radar_messages.RADAR_CALLBACK_PREFIX
MAX_US_NEW_CANDIDATES_PER_RUN = radar_scanners.MAX_US_NEW_CANDIDATES_PER_RUN
_safe_float = radar_rules.safe_float
_short_text = radar_messages.short_text
_is_experimental_yahoo_source = radar_rules.is_experimental_yahoo_source
_normalise_a_share_codes = radar_rules.normalise_a_share_codes
_is_a_share_trading_session = radar_rules.is_a_share_trading_session
_is_us_trading_session = radar_rules.is_us_trading_session
_market_label = radar_rules.market_label
_market_close_minutes = radar_rules.market_close_minutes
_market_session_start = radar_rules.market_session_start
_signal_text = radar_messages.signal_text
_candidate_buttons = radar_messages.candidate_buttons
_format_candidate_message = radar_messages.format_candidate_message
_format_update_message = radar_messages.format_update_message


def _send_candidate(candidate: dict[str, Any], store: RadarStore, now: datetime) -> bool:
    """Deliver a newly created candidate and persist its Telegram message id."""
    _set_run_summary(telegram_attempted=True)
    message_id = send_tg_interactive(
        _format_candidate_message(candidate),
        reply_markup=_candidate_buttons(str(candidate["candidate_id"])),
        token=settings.RADAR_INTERACTION_BOT_TOKEN,
        chat_id=settings.RADAR_INTERACTION_CHAT_ID,
    )
    if message_id is None:
        _set_run_summary(telegram_sent=False, status="failed")
        return False
    store.set_telegram_delivery(
        str(candidate["candidate_id"]),
        str(settings.RADAR_INTERACTION_CHAT_ID),
        message_id,
        now,
    )
    _set_run_summary(telegram_sent=True)
    return True


def _create_candidate(
    store: RadarStore,
    *,
    market: str,
    symbol: str,
    name: str,
    price: float,
    pct: Optional[float],
    volume: Optional[float],
    attributes: dict[str, Any],
    now: datetime,
) -> bool:
    """Apply mute/session limits before creating and delivering one candidate."""
    muted_until = store.suppressed_until(market, symbol, now)
    if muted_until is not None:
        log_info(
            f"雷达跳过已静默标的: {market}:{symbol}，静默至 {muted_until.isoformat()}"
        )
        return False
    session_start = _market_session_start(market, now)
    delivered_count = store.delivered_candidate_count_since(
        market, symbol, session_start, now
    )
    if delivered_count >= settings.RADAR_MAX_CANDIDATES_PER_SYMBOL_PER_SESSION:
        log_info(
            f"雷达跳过当日已推送标的: {market}:{symbol}，"
            f"已推送 {delivered_count} 次"
        )
        return False
    candidate, created = store.create_candidate(
        market=market,
        symbol=symbol,
        name=name,
        price=price,
        pct=pct,
        volume=volume,
        attributes=attributes,
        now=now,
        initial_track_minutes=settings.RADAR_INITIAL_TRACK_MINUTES,
    )
    if not created:
        return False
    if not _send_candidate(candidate, store, now):
        store.close_candidate(str(candidate["candidate_id"]), "Telegram 初始推送失败", now)
        return False
    return True


def _scan_a_share_candidates(store: RadarStore, now: datetime) -> tuple[int, int]:
    return radar_scanners.scan_a_share_candidates(
        store,
        now,
        normalise_codes=_normalise_a_share_codes,
        is_trading_session=_is_a_share_trading_session,
        get_stock_quote=get_stock_quote,
        safe_float=_safe_float,
        create_candidate=_create_candidate,
    )


def _scan_a_share_hot_pool(store: RadarStore, now: datetime) -> tuple[int, int]:
    return radar_scanners.scan_a_share_hot_pool(
        store,
        now,
        is_trading_session=_is_a_share_trading_session,
        get_hot_stocks_data=get_hot_stocks_data,
        get_data_source_health=get_data_source_health,
        safe_float=_safe_float,
        create_candidate=_create_candidate,
        set_run_reason=_set_run_reason,
    )


def _scan_yahoo_experimental_candidates(
    store: RadarStore, now: datetime
) -> tuple[int, int]:
    return radar_scanners.scan_yahoo_experimental_candidates(
        store,
        now,
        is_trading_session=_is_us_trading_session,
        fetch_candidates=fetch_yfinance_broad_market_candidates,
        create_candidate=_create_candidate,
        set_run_reason=_set_run_reason,
    )


def _scan_us_candidates(store: RadarStore, now: datetime) -> tuple[int, int]:
    return radar_scanners.scan_us_candidates(
        store,
        now,
        is_trading_session=_is_us_trading_session,
        scan_yahoo_candidates=_scan_yahoo_experimental_candidates,
        get_us_stock_snapshots=get_us_stock_snapshots,
        get_us_stock_news=get_us_stock_news,
        create_candidate=_create_candidate,
    )


def _fetch_candidate_quote(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Fetch one tracked symbol through the source recorded at creation time."""
    if candidate["market"] == "CN":
        quote = get_stock_quote(candidate["symbol"])
        if not quote:
            return None
        price = _safe_float(quote.get("price"))
        pct = _safe_float(quote.get("pct"))
        if price is None or price <= 0:
            return None
        return {
            "name": str(quote.get("name") or candidate["name"]),
            "price": price,
            "pct": pct,
            "volume": None,
        }
    attributes = candidate.get("attributes") or {}
    if _is_experimental_yahoo_source(attributes):
        try:
            quotes = fetch_yfinance_dev_quotes([str(candidate["symbol"])])
        except Exception as exc:
            log_error(f"❌ Yahoo 实验性追踪报价失败: {exc.__class__.__name__}")
            return None
        if not quotes:
            log_error("❌ Yahoo 实验性追踪报价为空")
            return None
        quote = quotes[0]
        return {
            "name": str(quote.get("name") or candidate["name"]),
            "price": quote.get("price"),
            "pct": quote.get("pct"),
        }
    return get_us_stock_quote(str(candidate["symbol"]))


def _send_tracking_update(candidate: dict[str, Any], state: str) -> bool:
    return _send_tg_with_summary(
        _format_update_message(candidate, state),
        token=settings.RADAR_INTERACTION_BOT_TOKEN,
        chat_id=settings.RADAR_INTERACTION_CHAT_ID,
    )


def _process_active_candidates(store: RadarStore, now: datetime) -> tuple[int, int, int]:
    return radar_tracking.process_active_candidates(
        store,
        now,
        is_a_share_trading_session=_is_a_share_trading_session,
        is_us_trading_session=_is_us_trading_session,
        fetch_candidate_quote=_fetch_candidate_quote,
        safe_float=_safe_float,
        send_update=_send_tracking_update,
    )


def _close_expired_candidates(store: RadarStore, now: datetime) -> int:
    return radar_tracking.close_expired_candidates(
        store, now, send_update=_send_tracking_update
    )


def _is_authorized_callback(callback: dict[str, Any]) -> bool:
    return is_authorized_interaction(
        callback, private_chat_id=settings.RADAR_INTERACTION_CHAT_ID
    )


def handle_radar_callback(
    callback: dict[str, Any], now: Optional[datetime] = None
) -> str:
    """Apply one button click and return a short Telegram callback notice."""
    now = now or datetime.now(settings.SHA_TZ)
    if not _is_authorized_callback(callback):
        return "此按钮仅允许配置的管理员使用。"
    parts = str(callback.get("data") or "").split(":")
    if len(parts) != 3 or parts[0] != RADAR_CALLBACK_PREFIX:
        return "未知的雷达操作。"
    candidate_id, action = parts[1], parts[2]
    store = RadarStore(settings.MONITOR_DB_FILE)
    store.initialize()
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        return "该候选已不存在。"
    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if candidate.get("telegram_chat_id") and str(chat.get("id")) != str(
        candidate["telegram_chat_id"]
    ):
        return "该按钮不属于当前追踪消息。"
    if action == "stop":
        if store.close_candidate(candidate_id, "用户停止追踪", now):
            from core.metrics import record_feedback_metric

            record_feedback_metric("radar", "stop")
            return f"已停止追踪 {candidate['symbol']}。"
        return "该候选已结束，无需重复停止。"
    if action == "mute":
        mute_days = settings.RADAR_SYMBOL_MUTE_DAYS
        store.suppress_symbol(
            str(candidate["market"]),
            str(candidate["symbol"]),
            until=now + timedelta(days=mute_days),
            reason="用户不感兴趣",
            now=now,
        )
        store.close_candidate(candidate_id, "用户不感兴趣", now)
        from core.metrics import record_feedback_metric

        record_feedback_metric("radar", "mute")
        return f"已停止追踪 {candidate['symbol']}，未来 {mute_days} 天不再推送。"
    if action == "close":
        minutes = _market_close_minutes(str(candidate["market"]), now)
        extended = store.extend_candidate(candidate_id, minutes, now)
        if extended:
            from core.metrics import record_feedback_metric

            record_feedback_metric("radar", "continue_to_close")
        return (
            f"已追踪 {candidate['symbol']} 至本市场收盘。"
            if extended
            else "该候选已结束，无法延长。"
        )
    try:
        minutes = int(action)
    except ValueError:
        return "未知的追踪时长。"
    if minutes not in {30, 60, 120, 240}:
        return "不允许的追踪时长。"
    extended = store.extend_candidate(candidate_id, minutes, now)
    if extended:
        from core.metrics import record_feedback_metric

        record_feedback_metric("radar", "continue_tracking")
    return (
        f"已继续追踪 {candidate['symbol']} {minutes} 分钟。"
        if extended
        else "该候选已结束，无法延长。"
    )


def run_radar() -> None:
    """Run one candidate-scan and tracking cycle; no order is ever submitted."""
    now = datetime.now(settings.SHA_TZ)
    if not (
        settings.RADAR_A_SHARE_CODES
        or settings.RADAR_A_SHARE_HOT_POOL_ENABLED
        or settings.POLYGON_API_KEY
        or settings.YFINANCE_EXPERIMENTAL_RADAR_ENABLED
    ):
        raise RuntimeError(
            "雷达未配置任何行情来源：请设置 RADAR_A_SHARE_CODES 或 POLYGON_API_KEY"
        )
    store = RadarStore(settings.MONITOR_DB_FILE)
    store.initialize()
    if not store.acquire_lock("radar", now):
        log_info("实时标的雷达跳过：上一轮尚未结束")
        return
    try:
        a_market_open = _is_a_share_trading_session(now)
        us_market_open = _is_us_trading_session(now)
        a_sampled, a_candidates = _scan_a_share_candidates(store, now)
        a_hot_sampled, a_hot_candidates = _scan_a_share_hot_pool(store, now)
        us_sampled, us_candidates = _scan_us_candidates(store, now)
        if a_market_open and settings.RADAR_A_SHARE_CODES and not a_sampled:
            raise RuntimeError("A 股雷达在交易时段未获取到有效行情")
        if us_market_open and settings.POLYGON_API_KEY and not us_sampled:
            raise RuntimeError("美股雷达在交易时段未获取到有效行情")
        processed, confirmed, invalidated = _process_active_candidates(store, now)
        expired = _close_expired_candidates(store, now)
        _record_quality_counts(
            quote_samples=a_sampled + a_hot_sampled + us_sampled,
            new_candidates=a_candidates + a_hot_candidates + us_candidates,
            tracked_candidates=processed,
            confirmed=confirmed,
            invalidated=invalidated,
            expired=expired,
        )
        _record_fetch_success(True)
        log_info(
            "雷达完成: "
            f"a_sampled={a_sampled}, a_hot_sampled={a_hot_sampled}, "
            f"us_sampled={us_sampled}, "
            f"new_candidates={a_candidates + a_hot_candidates + us_candidates}, "
            f"active_processed={processed}, confirmed={confirmed}, "
            f"invalidated={invalidated}, expired={expired}"
        )
    except Exception as exc:
        log_error(f"❌ 实时标的雷达失败: {exc.__class__.__name__}")
        raise
    finally:
        store.release_lock("radar")
