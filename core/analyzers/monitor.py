"""Rule-based news and watchlist monitor implementation."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional

from config import settings
from core.data_fetcher import get_data_source_health, get_news, get_stock_quote
from core.monitor_store import MonitorStore, news_event_key
from utils.notifier import log_info


SMALL_COMPANY_NEWS_CATEGORIES = {"company"}
SMALL_COMPANY_NEWS_IMPORTANCE = {"low"}
MONITOR_ALLOWED_IMPORTANCE = {"high", "高", "偏高"}
BLACK_SWAN_KEYWORDS = (
    "战争",
    "开战",
    "军事冲突",
    "导弹袭击",
    "空袭",
    "封锁",
    "恐怖袭击",
    "政变",
    "紧急状态",
    "核泄漏",
    "核设施",
    "金融危机",
    "流动性危机",
    "银行挤兑",
    "银行倒闭",
    "主权违约",
    "债务违约",
    "市场熔断",
    "交易熔断",
    "股灾",
    "闪崩",
    "交易所宕机",
    "交易中断",
    "重大地震",
    "海啸",
    "大规模疫情",
    "war",
    "military strike",
    "missile",
    "airstrike",
    "blockade",
    "terror attack",
    "coup",
    "nuclear",
    "financial crisis",
    "liquidity crisis",
    "bank run",
    "bank collapse",
    "sovereign default",
    "market crash",
    "circuit breaker",
    "exchange outage",
    "earthquake",
    "tsunami",
    "pandemic",
)
BLACK_SWAN_STRONG_PHRASES = (
    "正式开战",
    "宣布开战",
    "进入紧急状态",
    "触发熔断",
    "发生强震",
    "发生海啸",
    "宣布破产",
    "主权违约",
    "bank run",
    "declared war",
    "state of emergency",
    "circuit breaker triggered",
)
BLACK_SWAN_CONTEXT_EXCLUSIONS = (
    "历史回顾",
    "周年纪念",
    "军事演习",
    "模拟演练",
    "电影",
    "电视剧",
    "游戏",
    "小说",
    "未经证实",
    "网传",
)
TRUSTED_URGENT_SOURCE_MARKERS = (
    "eastmoney",
    "reuters",
    "apnews",
    "bbc.",
    "ft.com",
    "wsj.com",
    "bloomberg",
    "gov.",
)
SMALL_COMPANY_NEWS_HIGH_IMPACT_KEYWORDS = (
    "停牌",
    "复牌",
    "并购",
    "重组",
    "退市",
    "立案",
    "证监会",
    "重大资产",
    "控制权",
    "暴雷",
)


def _is_monitor_alert_importance(item: dict[str, Any]) -> bool:
    """Return True only for news importance levels the monitor should send."""
    importance = str(item.get("importance") or "").strip().lower()
    return importance in MONITOR_ALLOWED_IMPORTANCE


def _is_black_swan_candidate(item: dict[str, Any]) -> bool:
    """Score source, wording and context before urgent AI review."""
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    if not any(keyword.lower() in text for keyword in BLACK_SWAN_KEYWORDS):
        return False

    score = 2
    if any(phrase.lower() in text for phrase in BLACK_SWAN_STRONG_PHRASES):
        score += 2
    if _is_trusted_urgent_source(item):
        score += 1
    if _is_monitor_alert_importance(item):
        score += 1
    if any(term.lower() in text for term in BLACK_SWAN_CONTEXT_EXCLUSIONS):
        score -= 2
    return score >= 3


def _is_trusted_urgent_source(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "").lower()
    host = urlparse(str(item.get("link") or "")).netloc.lower()
    combined = f"{source} {host}"
    return any(marker in combined for marker in TRUSTED_URGENT_SOURCE_MARKERS)


def _is_low_value_company_news(item: dict[str, Any]) -> bool:
    """Return True for ordinary single-company updates that should not be pushed."""
    category = str(item.get("category") or "").strip().lower()
    importance = str(item.get("importance") or "").strip().lower()
    scope = str(item.get("market_scope") or "").strip()
    text = f"{item.get('title', '')} {item.get('digest', '')}"

    if any(keyword in text for keyword in SMALL_COMPANY_NEWS_HIGH_IMPACT_KEYWORDS):
        return False

    return (
        category in SMALL_COMPANY_NEWS_CATEGORIES
        and importance in SMALL_COMPANY_NEWS_IMPORTANCE
        and scope in {"", "公司", "其他"}
    )


def _news_alert_severity(item: dict[str, Any]) -> Optional[str]:
    """Classify only high-value news for deterministic, low-latency delivery."""
    if _is_black_swan_candidate(item):
        return "紧急"
    if _is_low_value_company_news(item) or not _is_monitor_alert_importance(item):
        return None

    category = str(item.get("category") or "").strip().lower()
    scope = str(item.get("market_scope") or "").strip()
    text = f"{item.get('title', '')} {item.get('digest', '')}"
    is_major_company_event = any(
        keyword in text for keyword in SMALL_COMPANY_NEWS_HIGH_IMPACT_KEYWORDS
    )
    if category != "company" or scope not in {"", "公司", "其他"} or is_major_company_event:
        return "重要"
    return None


def _is_news_in_alert_window(item: dict[str, Any], now: datetime) -> bool:
    """Keep normal alerts fresh while allowing a short late window for emergencies."""
    published_at = item.get("datetime")
    if not isinstance(published_at, datetime):
        return False
    if published_at >= now - timedelta(minutes=settings.MONITOR_NEWS_FRESH_MINUTES):
        return True
    return _is_black_swan_candidate(item) and published_at >= now - timedelta(
        minutes=settings.MONITOR_NEWS_LOOKBACK_MINUTES
    )


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
    """Avoid treating closed-market snapshots as fresh one-minute price changes."""
    if now.weekday() >= 5:
        return False
    current_time = now.time().replace(tzinfo=None)
    return time(9, 30) <= current_time <= time(11, 30) or time(13, 0) <= current_time <= time(15, 0)


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
        sent = _send_tg_with_summary(
            content,
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
    except Exception as exc:
        store.mark_alert_failed(alert_key, now, exc.__class__.__name__)
        raise

    if sent:
        store.mark_alert_sent(alert_key, now)
        return True

    store.mark_alert_failed(alert_key, now, "telegram send returned false")
    return False


def _build_news_alert(item: dict[str, Any], severity: str) -> str:
    from core.formatter import _format_market_message, _format_news_time

    is_urgent = severity == "紧急"
    impact = (
        "触发跨市场风险关键词。请优先核对权威原文与后续公告，关注风险是否持续扩散。"
        if is_urgent
        else "触发政策、宏观或市场级重要性规则。请结合原文确认影响范围与持续性。"
    )
    return _format_market_message(
        "紧急市场提醒" if is_urgent else "重要市场提醒",
        report_time=_format_news_time(item),
        source=str(item.get("source") or "未知"),
        category=str(item.get("category") or "其他"),
        importance="高（紧急）" if is_urgent else "高",
        summary=str(item.get("title") or "未知新闻"),
        impact=impact,
        links=str(item.get("link") or "未知"),
        market_scope=str(item.get("market_scope") or "其他"),
        related_sectors=item.get("related_sectors"),
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


def _run_watchlist_monitor(store: MonitorStore, now: datetime) -> tuple[int, int]:
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
        if _claim_and_send(
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
    from core.runtime import _print_monitor_filter_summary, _record_news_summary

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

    sent_news = 0
    for item, severity in eligible_news[:3]:
        event_key = news_event_key(item)
        if _claim_and_send(
            store,
            alert_key=f"news:{event_key}",
            dedup_key=f"news:{event_key}",
            alert_type="news",
            severity=severity,
            content=_build_news_alert(item, severity),
            payload=item,
            now=now,
        ):
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
    sent_total = sent_news + sent_price + health_sent
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
            else f"news_sent={sent_news}, quote_samples={quote_count}, price_sent={sent_price}"
        ),
    )
