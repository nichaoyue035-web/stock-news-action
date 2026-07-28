"""Monitor-mode analyzer implementation."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any

from config import settings
from core.data_fetcher import get_news
from utils.notifier import log_info


SMALL_COMPANY_NEWS_CATEGORIES = {"company"}
SMALL_COMPANY_NEWS_IMPORTANCE = {"low"}
MONITOR_ALLOWED_IMPORTANCE = {"high", "高", "偏高"}
MONITOR_SEEN_TTL = timedelta(hours=6)
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
    """Return whether a headline warrants a black-swan-level urgent review."""
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    return any(keyword.lower() in text for keyword in BLACK_SWAN_KEYWORDS)


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


def _monitor_news_key(item: dict[str, Any]) -> str:
    """Return a stable key for preventing repeated monitor alerts."""
    return re.sub(r"\s+", " ", str(item.get("title") or "")).strip()


def _load_recent_monitor_alerts(now: datetime) -> dict[str, float]:
    """Load unexpired alert keys from previous monitor runs."""
    try:
        with open(settings.MONITOR_STATE_FILE, "r", encoding="utf-8") as file:
            raw_alerts = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log_info(f"监控去重状态读取失败，将继续本次检查: {exc.__class__.__name__}")
        return {}

    if not isinstance(raw_alerts, dict):
        log_info("监控去重状态格式无效，将继续本次检查")
        return {}

    cutoff = (now - MONITOR_SEEN_TTL).timestamp()
    recent_alerts: dict[str, float] = {}
    for key, timestamp in raw_alerts.items():
        try:
            parsed_timestamp = float(timestamp)
        except (TypeError, ValueError):
            continue
        if key and parsed_timestamp >= cutoff:
            recent_alerts[str(key)] = parsed_timestamp
    return recent_alerts


def _record_monitor_alerts(
    recent_alerts: dict[str, float], items: list[dict[str, Any]], now: datetime
) -> None:
    """Persist delivered alert titles so later polling runs do not resend them."""
    timestamp = now.timestamp()
    for item in items:
        key = _monitor_news_key(item)
        if key:
            recent_alerts[key] = timestamp

    temp_file = f"{settings.MONITOR_STATE_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(recent_alerts, file, ensure_ascii=False, sort_keys=True)
        os.replace(temp_file, settings.MONITOR_STATE_FILE)
    except OSError as exc:
        log_info(f"监控去重状态保存失败: {exc.__class__.__name__}")


def _filter_unseen_monitor_news(
    news: list[dict[str, Any]], recent_alerts: dict[str, float]
) -> list[dict[str, Any]]:
    """Keep items that have not already been delivered within the dedup window."""
    return [
        item
        for item in news
        if not (key := _monitor_news_key(item)) or key not in recent_alerts
    ]


def run_monitor(prompts: dict[str, str]) -> None:
    """Run real-time monitor analysis mode."""
    from core.formatter import (
        _display_category,
        _display_importance,
        _format_market_message,
        _format_news_time,
        _infer_market_importance,
        _infer_news_category,
        _title_icon,
    )
    from core.runtime import (
        _print_monitor_filter_summary,
        _record_news_summary,
        _send_health_status,
        _send_tg_with_summary,
    )
    from core.analyzer import _get_ai_response_with_health, _has_effective_content

    news = get_news(20, semantic_dedup=False, translate_external=False)
    _record_news_summary(news)
    input_items = len(news)
    if not news:
        _print_monitor_filter_summary(
            input_items=input_items,
            after_time_filter=0,
            after_keyword_filter=0,
            after_dedup=0,
            final_alert_items=0,
            decision="skip",
            reason="no input news",
        )
        _send_health_status(
            "新闻数据为空，无法生成监控摘要",
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
        return
    now = datetime.now(settings.SHA_TZ)
    strict_threshold = now - timedelta(minutes=5)
    soft_threshold = now - timedelta(minutes=20)

    fresh_news: list[dict[str, Any]] = []
    after_time_filter = 0
    for item in news:
        if item["datetime"] >= strict_threshold:
            after_time_filter += 1
            fresh_news.append(item)
        elif item["datetime"] >= soft_threshold and _is_black_swan_candidate(item):
            fresh_news.append(item)

    after_keyword_filter = len(fresh_news)
    if not fresh_news:
        _print_monitor_filter_summary(
            input_items=input_items,
            after_time_filter=after_time_filter,
            after_keyword_filter=after_keyword_filter,
            after_dedup=0,
            final_alert_items=0,
            decision="skip",
            reason="no recent market news in time window",
        )
        log_info("未发现符合时间窗口的市场信息，跳过推送")
        return

    filtered_news = [item for item in fresh_news if not _is_low_value_company_news(item)]
    filtered_company_items = len(fresh_news) - len(filtered_news)
    if filtered_company_items:
        log_info(f"监控过滤普通公司消息: skipped={filtered_company_items}")

    if not filtered_news:
        _print_monitor_filter_summary(
            input_items=input_items,
            after_time_filter=after_time_filter,
            after_keyword_filter=0,
            after_dedup=0,
            final_alert_items=0,
            decision="skip",
            reason="only ordinary low-importance company news after filters",
        )
        log_info("仅发现普通低重要性公司消息，跳过推送")
        return

    black_swan_news = [
        item for item in filtered_news if _is_black_swan_candidate(item)
    ]
    filtered_non_black_swan = len(filtered_news) - len(black_swan_news)
    if filtered_non_black_swan:
        log_info(f"黑天鹅监控过滤普通消息: skipped={filtered_non_black_swan}")

    if not black_swan_news:
        _print_monitor_filter_summary(
            input_items=input_items,
            after_time_filter=after_time_filter,
            after_keyword_filter=0,
            after_dedup=0,
            final_alert_items=0,
            decision="skip",
            reason="no black-swan-level news after filters",
        )
        log_info("未发现黑天鹅级重大突发，跳过推送")
        return

    after_keyword_filter = len(black_swan_news)
    dedup_news: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in black_swan_news:
        if item["title"] not in seen_titles:
            seen_titles.add(item["title"])
            dedup_news.append(item)

    after_dedup = len(dedup_news)
    recent_alerts = _load_recent_monitor_alerts(now)
    dedup_news = _filter_unseen_monitor_news(dedup_news, recent_alerts)
    if not dedup_news:
        _print_monitor_filter_summary(
            input_items=input_items,
            after_time_filter=after_time_filter,
            after_keyword_filter=after_keyword_filter,
            after_dedup=after_dedup,
            final_alert_items=0,
            decision="skip",
            reason="all candidate alerts were already delivered recently",
        )
        log_info("候选重要消息近期已推送，跳过重复提醒")
        return

    after_dedup = len(dedup_news)
    news_titles = [
        (
            f"{i}. [{n.get('source', 'unknown')}] "
            f"[分类:{_display_category(n.get('category'))} / "
            f"重要性:{_display_importance(n.get('importance'))} / "
            f"范围:{n.get('market_scope') or '其他'}] "
            f"{n['title']} (详情:{n['digest'][:60]})"
        )
        for i, n in enumerate(dedup_news[:12])
    ]
    prompt = prompts.get("monitor", settings.DEFAULT_PROMPTS["monitor"]).format(
        news_list="\n".join(news_titles)
    )
    content = _get_ai_response_with_health(
        f"{prompt}\n\n【黑天鹅模式】候选已由规则筛出。只有可能导致跨市场急剧波动、"
        "系统性风险或重大地缘冲突的事件才能输出 ALERT；日常政策、业绩、"
        "行业消息、普通公司公告一律不输出 ALERT。"
    )
    if not content:
        _print_monitor_filter_summary(
            input_items=input_items,
            after_time_filter=after_time_filter,
            after_keyword_filter=after_keyword_filter,
            after_dedup=after_dedup,
            final_alert_items=0,
            decision="skip",
            reason="ai returned empty monitor summary",
        )
        log_info("DeepSeek 没有生成有效监控摘要，跳过推送")
        return

    alerts_buffer: list[str] = []
    alert_items: list[dict[str, Any]] = []
    for line in content.split("\n"):
        if "ALERT|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        try:
            idx = int(re.sub(r"\D", "", parts[1]))
        except ValueError:
            continue
        if idx < len(dedup_news):
            item = dedup_news[idx]
            alert_items.append(item)
            link = str(item.get("link") or "").strip()
            alerts_buffer.append(
                _format_market_message(
                    "市场信息摘要",
                    report_time=_format_news_time(item),
                    source=str(item.get("source") or "未知"),
                    category=_infer_news_category(item),
                    importance=_infer_market_importance(item),
                    summary=str(item.get("title") or "未知"),
                    impact=parts[2],
                    links=link or "未知",
                    market_scope=str(item.get("market_scope") or "其他"),
                    related_sectors=item.get("related_sectors"),
                    include_title=False,
                )
            )

    final_alert_items = len(alerts_buffer[:3])
    if alerts_buffer:
        msg = (
            f"{_title_icon('市场信息摘要')} 市场信息摘要\n\n"
            + "\n\n〰️〰️〰️\n\n".join(alerts_buffer[:3])
        )
        if _has_effective_content(msg):
            _print_monitor_filter_summary(
                input_items=input_items,
                after_time_filter=after_time_filter,
                after_keyword_filter=after_keyword_filter,
                after_dedup=after_dedup,
                final_alert_items=final_alert_items,
                decision="send",
            )
            delivered = _send_tg_with_summary(
                msg,
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
            if delivered:
                _record_monitor_alerts(recent_alerts, alert_items[:3], now)
        else:
            _print_monitor_filter_summary(
                input_items=input_items,
                after_time_filter=after_time_filter,
                after_keyword_filter=after_keyword_filter,
                after_dedup=after_dedup,
                final_alert_items=final_alert_items,
                decision="skip",
                reason="final telegram body empty",
            )
            _send_health_status(
                "最终 Telegram 正文为空",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
    else:
        _print_monitor_filter_summary(
            input_items=input_items,
            after_time_filter=after_time_filter,
            after_keyword_filter=after_keyword_filter,
            after_dedup=after_dedup,
            final_alert_items=0,
            decision="skip",
            reason="ai returned no alert lines",
        )
        log_info("DeepSeek 未识别需提醒的市场信息，跳过推送")
