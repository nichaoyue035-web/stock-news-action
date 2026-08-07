"""Daily-mode analyzer implementation."""

from __future__ import annotations

from datetime import datetime

from config import settings
from core.data_fetcher import get_news
from core.market_calendar import is_cn_a_share_trading_day
from utils.notifier import log_info


def run_daily(prompts: dict[str, str]) -> None:
    """Run daily market news analysis mode."""
    from core.formatter import (
        _format_links,
        _format_market_message,
        _format_news_facts,
        _format_news_prompt_line,
        _format_sources,
        format_ai_insight,
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
    if not is_cn_a_share_trading_day(now):
        log_info(f"A 股休市，跳过每日摘要：{now.strftime('%Y-%m-%d')}")
        _set_run_reason("market closed", status="success")
        return
    news = get_news(1440)
    _record_news_summary(news)
    if not news:
        _send_health_status("新闻数据为空，无法生成每日摘要")
        return
    news_txt = "\n".join(
        [_format_news_prompt_line(n, include_time=True) for n in news[:30]]
    )
    content = _get_ai_response_with_health(
        prompts.get("daily", settings.DEFAULT_PROMPTS["daily"]).format(
            news_txt=news_txt,
            report_date=now.strftime("%Y-%m-%d"),
            report_time=now.strftime("%Y-%m-%d %H:%M"),
        ),
        model="deepseek-reasoner",
    )
    title = "A股盘前简报"
    facts = _format_news_facts(news, limit=5)
    if content:
        impact = format_ai_insight(content)
    else:
        _set_run_reason("DeepSeek 没有生成有效摘要", status="partial")
        title = "A股盘前简报（AI解读暂不可用）"
        impact = "**AI解读：** 本次暂不可用，以下仅保留可核对新闻事实。"

    _send_tg_with_summary(
        _format_market_message(
            title,
            report_time=now.strftime("%Y-%m-%d %H:%M"),
            source=_format_sources(news, "东方财富 / RSS"),
            category="other",
            importance="medium",
            summary=f"**重点新闻：**\n{facts}",
            impact=impact,
            links=_format_links([item.get("link") for item in news[:5]]),
            market_scope="A股",
            related_sectors=[
                sector
                for item in news[:20]
                for sector in item.get("related_sectors", [])
            ][:6],
        )
    )
