"""Periodic-mode analyzer implementation."""

from __future__ import annotations

from datetime import datetime

from config import settings
from core.data_fetcher import get_news
from core.market_calendar import is_cn_a_share_trading_day
from utils.notifier import log_info


def run_periodic(prompts: dict[str, str]) -> None:
    """Run periodic intraday summary mode."""
    from core.formatter import (
        _format_links,
        _format_market_message,
        _format_news_facts,
        _format_news_prompt_line,
        _format_sources,
        _format_weekday,
    )
    from core.runtime import (
        _record_news_summary,
        _send_health_status,
        _send_tg_with_summary,
        _set_run_reason,
    )
    from core.analyzer import (
        _get_ai_response_with_health,
    )

    now = datetime.now(settings.SHA_TZ)
    report_weekday = _format_weekday(now)
    if not is_cn_a_share_trading_day(now):
        log_info(f"A 股休市，跳过盘中简报：{now.strftime('%Y-%m-%d')}")
        _set_run_reason("market closed", status="success")
        return

    news = get_news(240)
    _record_news_summary(news)
    if not news:
        _send_health_status("新闻数据为空，无法生成市场简报")
        return

    news_txt = "\n".join(
        [_format_news_prompt_line(n, include_time=False) for n in news[:25]]
    )

    title = "🍵 盘中茶歇"
    content = _get_ai_response_with_health(
        prompts.get("periodic", settings.DEFAULT_PROMPTS["periodic"]).format(
            news_txt=news_txt,
            report_date=now.strftime("%Y-%m-%d"),
            report_time=now.strftime("%Y-%m-%d %H:%M"),
            report_weekday=report_weekday,
        ),
        model="deepseek-chat",
    )
    if content:
        _send_tg_with_summary(
            _format_market_message(
                title,
                report_time=now.strftime("%Y-%m-%d %H:%M"),
                source=_format_sources(news, "东方财富 / RSS"),
                category="盘中",
                importance="低（盘中简报）",
                summary=f"重点新闻：\n{_format_news_facts(news, limit=5)}",
                impact=content,
                links=_format_links([item.get("link") for item in news[:5]]),
                market_scope="A股",
                related_sectors=[
                    sector
                    for item in news[:20]
                    for sector in item.get("related_sectors", [])
                ][:6],
            )
        )
    else:
        _send_health_status("DeepSeek 没有生成有效摘要")
