"""Rule-based news and watchlist monitor implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from config import settings
from core.analyzers import (
    monitor_messages,
    monitor_prices,
    monitor_rules,
    monitor_tracking,
)
from core.analyzers.monitor_tracking import (
    _news_tracking_buttons,
    _process_active_news_trackers,
)
from core.data_fetcher import get_data_source_health, get_news, get_stock_quote
from core.monitor_store import MonitorStore, news_event_key
from utils.notifier import log_info, send_tg_interactive

# Compatibility exports: callers keep importing monitor helpers from this facade.
MARKET_ALERT_DEDUP_SEVERITIES = monitor_rules.MARKET_ALERT_DEDUP_SEVERITIES
NEWS_TRACK_CALLBACK_PREFIX = monitor_tracking.NEWS_TRACK_CALLBACK_PREFIX
NEWS_TRACK_MINUTES = monitor_tracking.NEWS_TRACK_MINUTES
_is_monitor_alert_importance = monitor_rules._is_monitor_alert_importance
_contains_risk_term = monitor_rules._contains_risk_term
_is_unverified_black_swan = monitor_rules._is_unverified_black_swan
_has_excluded_black_swan_context = monitor_rules._has_excluded_black_swan_context
_black_swan_score = monitor_rules._black_swan_score
_is_black_swan_candidate = monitor_rules._is_black_swan_candidate
_is_trusted_urgent_source = monitor_rules._is_trusted_urgent_source
_black_swan_alert_severity = monitor_rules._black_swan_alert_severity
_is_low_value_company_news = monitor_rules._is_low_value_company_news
is_three_hour_market_summary_item = monitor_rules.is_three_hour_market_summary_item
_news_alert_severity = monitor_rules._news_alert_severity
_is_news_in_alert_window = monitor_rules._is_news_in_alert_window
_normalise_market_event_text = monitor_rules._normalise_market_event_text
_market_event_numbers = monitor_rules._market_event_numbers
_same_market_event = monitor_rules._same_market_event
_is_recent_market_alert_duplicate = monitor_rules._is_recent_market_alert_duplicate

_build_news_alert = monitor_messages._build_news_alert
_compact_alert_text = monitor_messages._compact_alert_text
_compact_market_insight = monitor_messages._compact_market_insight
_format_compact_market_alert = monitor_messages._format_compact_market_alert
_related_sector_text = monitor_messages._related_sector_text
_black_swan_impact_profile = monitor_messages._black_swan_impact_profile
_important_market_impact_profile = monitor_messages._important_market_impact_profile
_build_monitor_impact = monitor_messages._build_monitor_impact



def _claim_and_send(
    store: MonitorStore,
    *,
    alert_key: str,
    dedup_key: str,
    alert_type: str,
    severity: str,
    content: str,
    payload: dict[str, Any],
    now: datetime,
    cooldown_minutes: int = 0,
    reply_markup: Optional[dict[str, Any]] = None,
) -> bool:
    """Send only a claimed alert and leave a failed delivery retryable."""
    from core.runtime import _send_tg_with_summary

    if not store.claim_alert(
        alert_key=alert_key,
        dedup_key=dedup_key,
        alert_type=alert_type,
        severity=severity,
        payload=payload,
        now=now,
        cooldown_minutes=cooldown_minutes,
    ):
        return False

    try:
        if reply_markup is None:
            sent = _send_tg_with_summary(
                content,
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
        else:
            from core.runtime import _set_run_summary

            _set_run_summary(telegram_attempted=True)
            sent = (
                send_tg_interactive(
                    content,
                    reply_markup=reply_markup,
                    token=settings.TG_BOT_TOKEN_MONITOR,
                    chat_id=settings.TG_CHAT_ID_MONITOR,
                )
                is not None
            )
            _set_run_summary(telegram_sent=sent, **({"status": "failed"} if not sent else {}))
    except Exception as exc:
        store.mark_alert_failed(alert_key, now, exc.__class__.__name__)
        raise

    if sent:
        store.mark_alert_sent(alert_key, now)
        return True

    store.mark_alert_failed(alert_key, now, "telegram send returned false")
    return False


def _send_monitor_health_alert(
    store: MonitorStore, reason: str, now: datetime
) -> bool:
    """Report an actual data failure at a limited cadence instead of every minute."""
    from core.formatter import _format_market_message, _format_source_health_line
    from core.runtime import _format_health_status_message, _set_run_reason

    _set_run_reason(reason, status="failed")
    health_details = _format_health_status_message(reason, _format_source_health_line)
    content = _format_market_message(
        "实时监控状态",
        report_time=now.strftime("%Y-%m-%d %H:%M"),
        source="监控数据源",
        category="系统",
        importance="高",
        summary="实时新闻抓取没有返回可用内容。",
        impact=health_details,
        links="未知",
        market_scope="系统",
    )
    bucket = now.strftime("%Y%m%d%H") + str(now.minute // 15)
    return _claim_and_send(
        store,
        alert_key=f"health:news-fetch:{bucket}",
        dedup_key="health:news-fetch",
        alert_type="health",
        severity="high",
        content=content,
        payload={"reason": reason, "health": get_data_source_health()},
        now=now,
        cooldown_minutes=15,
    )


# Compatibility exports for existing callers and tests.
_safe_float = monitor_prices._safe_float
_normalise_watchlist_codes = monitor_prices._normalise_watchlist_codes
_is_a_share_trading_session = monitor_prices._is_a_share_trading_session
_build_price_alert = monitor_prices._build_price_alert
_format_news_tracking_end = monitor_tracking._format_news_tracking_end
_format_news_tracking_update = monitor_tracking._format_news_tracking_update
_is_related_tracked_news = monitor_tracking._is_related_tracked_news
_tracking_terms = monitor_tracking._tracking_terms
handle_news_tracking_callback = monitor_tracking.handle_news_tracking_callback

def _run_watchlist_monitor(store: MonitorStore, now: datetime) -> tuple[int, int]:
    """Run quote monitoring with dependencies kept at the orchestration boundary."""
    return monitor_prices.run_watchlist_monitor(
        store,
        now,
        get_stock_quote=get_stock_quote,
        claim_and_send=_claim_and_send,
    )


def run_monitor(_prompts: dict[str, str]) -> None:
    """Run one minute-monitor cycle for news and configured watchlist quotes."""
    now = datetime.now(settings.SHA_TZ)
    store = MonitorStore(settings.MONITOR_DB_FILE)
    store.initialize()
    if not store.acquire_lock("monitor", now):
        log_info("实时监控跳过：上一轮尚未结束")
        return

    try:
        _run_monitor_cycle(store, now)
    finally:
        store.release_lock("monitor")


def _run_monitor_cycle(store: MonitorStore, now: datetime) -> None:
    """Process one claimed monitor cycle after the overlapping-run guard succeeds."""
    from core.runtime import (
        _print_monitor_filter_summary,
        _record_news_summary,
        _record_quality_counts,
    )

    news = get_news(
        settings.MONITOR_NEWS_LOOKBACK_MINUTES,
        semantic_dedup=False,
        translate_external=False,
    )
    _record_news_summary(news)

    input_items = len(news)
    after_time_filter = 0
    eligible_news: list[tuple[dict[str, Any], str]] = []
    recorded_news = 0
    for item in news:
        if store.record_news_event(item, now):
            recorded_news += 1
        if not _is_news_in_alert_window(item, now):
            continue
        after_time_filter += 1
        severity = _news_alert_severity(item)
        if severity:
            eligible_news.append((item, severity))

    tracking_updates, tracking_ended = _process_active_news_trackers(store, now)

    sent_news = 0
    suppressed_duplicates = 0
    for item, severity in eligible_news:
        if sent_news >= 3:
            break
        if _is_recent_market_alert_duplicate(store, item, severity, now):
            suppressed_duplicates += 1
            log_info(
                "市场提醒去重：同级别近期已发送相同或高度相似事件，跳过重复投递"
            )
            continue
        event_key = news_event_key(item)
        interactive_alert = (
            settings.MARKET_ALERT_INTERACTION_ENABLED
            and severity in MARKET_ALERT_DEDUP_SEVERITIES
        )
        if _claim_and_send(
            store,
            alert_key=f"news:{event_key}",
            dedup_key=f"news:{event_key}",
            alert_type="news",
            severity=severity,
            content=_build_news_alert(item, severity),
            payload=item,
            now=now,
            reply_markup=(
                _news_tracking_buttons(event_key, str(item.get("link") or ""))
                if interactive_alert
                else None
            ),
        ):
            if interactive_alert:
                store.offer_news_tracking(
                    event_key=event_key,
                    item=item,
                    telegram_chat_id=str(settings.TG_CHAT_ID_MONITOR),
                    now=now,
                )
            sent_news += 1

    health_sent = 0
    if not news:
        health = get_data_source_health()
        if any(state.get("status") == "failed" for state in health.values()):
            health_sent = int(
                _send_monitor_health_alert(store, "新闻数据源没有返回可用内容", now)
            )
        else:
            log_info("新闻监控无新快讯，跳过推送")

    quote_count, sent_price = _run_watchlist_monitor(store, now)
    sent_total = sent_news + sent_price + health_sent + tracking_updates + tracking_ended
    _record_quality_counts(
        input_items=input_items,
        timely_items=after_time_filter,
        eligible_items=len(eligible_news),
        new_items=recorded_news,
        duplicate_alerts_suppressed=suppressed_duplicates,
        alerts_sent=sent_total,
        quote_samples=quote_count,
    )
    _print_monitor_filter_summary(
        input_items=input_items,
        after_time_filter=after_time_filter,
        after_keyword_filter=len(eligible_news),
        after_dedup=recorded_news,
        final_alert_items=sent_total,
        decision="send" if sent_total else "skip",
        reason=(
            "no new eligible news or watchlist price signal"
            if not sent_total
            else (
                f"news_sent={sent_news}, news_dedup_suppressed={suppressed_duplicates}, "
                f"tracking_updates={tracking_updates}, tracking_ended={tracking_ended}, "
                f"quote_samples={quote_count}, price_sent={sent_price}"
            )
        ),
    )
