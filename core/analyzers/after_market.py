"""After-market analyzer implementation."""

from __future__ import annotations

from datetime import datetime

from config import settings
from core.data_fetcher import get_news
from core.market_calendar import is_cn_a_share_trading_day
from utils.notifier import log_info


def run_after_market(prompts: dict[str, str]) -> None:
    """Run after-market review mode."""
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

    mode = "after_market"
    now = datetime.now(settings.SHA_TZ)
    report_weekday = _format_weekday(now)
    if mode == "after_market" and not is_cn_a_share_trading_day(now):
        log_info(
            f"休市跳过：{now.strftime('%Y-%m-%d')} {report_weekday} A股休市，每日复盘不发送"
        )
        _set_run_reason("market closed", status="success")
        return

    news = get_news(240)
    _record_news_summary(news)
    if not news:
        _send_health_status("新闻数据为空，无法生成市场简报")
        return

    if mode == "after_market":
        news_txt = "\n".join(
            [_format_news_prompt_line(n, include_time=True) for n in news[:25]]
        )
    else:
        news_txt = "\n".join(
            [_format_news_prompt_line(n, include_time=False) for n in news[:25]]
        )

    title = "🌇 每日复盘" if mode == "after_market" else "🍵 盘中茶歇"
    content = _get_ai_response_with_health(
        prompts.get(mode, settings.DEFAULT_PROMPTS[mode]).format(
            news_txt=news_txt,
            report_date=now.strftime("%Y-%m-%d"),
            report_time=now.strftime("%Y-%m-%d %H:%M"),
            report_weekday=report_weekday,
        ),
        model="deepseek-reasoner" if mode == "after_market" else "deepseek-chat",
    )
    if content:
        category = "复盘" if mode == "after_market" else "盘中"
        importance = (
            "中（市场复盘）" if mode == "after_market" else "低（盘中简报）"
        )
        _send_tg_with_summary(
            _format_market_message(
                title,
                report_time=now.strftime("%Y-%m-%d %H:%M"),
                source=_format_sources(news, "东方财富 / RSS"),
                category=category,
                importance=importance,
                summary=f"重点新闻：\n{_format_news_facts(news, limit=6)}",
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
