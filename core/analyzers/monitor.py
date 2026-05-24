"""Monitor-mode analyzer implementation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from config import settings
from core.data_fetcher import get_news


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
    from core.analyzer import (
        HIGH_IMPACT_KEYWORDS,
        _get_ai_response_with_health,
        _has_effective_content,
    )

    news = get_news(90)
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
    strict_threshold = now - timedelta(minutes=15)
    soft_threshold = now - timedelta(minutes=30)

    fresh_news: list[dict[str, Any]] = []
    after_time_filter = 0
    for item in news:
        if item["datetime"] >= strict_threshold:
            after_time_filter += 1
            fresh_news.append(item)
        elif item["datetime"] >= soft_threshold and any(
            keyword in f"{item['title']} {item['digest']}"
            for keyword in HIGH_IMPACT_KEYWORDS
        ):
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
            reason="no important market news in time window",
        )
        _send_health_status(
            "未发现符合时间窗口的重要市场信息",
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
        return

    dedup_news: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in fresh_news:
        if item["title"] not in seen_titles:
            seen_titles.add(item["title"])
            dedup_news.append(item)

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
    content = _get_ai_response_with_health(
        prompts.get("monitor", settings.DEFAULT_PROMPTS["monitor"]).format(
            news_list="\n".join(news_titles)
        )
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
        _send_health_status(
            "DeepSeek 没有生成有效摘要",
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
        return

    alerts_buffer: list[str] = []
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
            _send_tg_with_summary(
                msg,
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
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
        _send_health_status(
            "DeepSeek 未识别需提醒的市场信息",
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
