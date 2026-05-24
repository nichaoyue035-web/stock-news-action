"""Funds-mode analyzer implementation."""

from __future__ import annotations

from datetime import datetime

from config import settings
from core.data_fetcher import get_market_funds, get_news


def run_funds(prompts: dict[str, str]) -> None:
    """Run funds flow analysis mode."""
    from core.formatter import (
        _format_links,
        _format_market_message,
        _format_news_prompt_line,
        _format_sources,
    )
    from core.runtime import (
        _record_fetch_success,
        _record_news_summary,
        _send_health_status,
        _send_tg_with_summary,
    )
    from core.analyzer import (
        _get_ai_response_with_health,
    )

    now = datetime.now(settings.SHA_TZ)
    top_in, top_out = get_market_funds()
    _record_fetch_success(bool(top_in))
    if not top_in:
        _send_health_status("资金流数据为空，无法生成资金流摘要")
        return
    in_str = "\n".join(
        [f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in]
    )
    out_str = "\n".join(
        [f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out]
    )
    news = get_news(720)
    _record_news_summary(news)
    news_txt = "\n".join(
        [_format_news_prompt_line(n, include_time=True) for n in news[:20]]
    )
    content = _get_ai_response_with_health(
        prompts.get("funds", settings.DEFAULT_PROMPTS["funds"]).format(
            in_str=in_str,
            out_str=out_str,
            news_txt=news_txt or "无重要消息",
            report_date=now.strftime("%Y-%m-%d"),
            report_time=now.strftime("%Y-%m-%d %H:%M"),
        ),
        model="deepseek-reasoner",
    )
    if content:
        _send_tg_with_summary(
            _format_market_message(
                "主力资金雷达",
                report_time=now.strftime("%Y-%m-%d %H:%M"),
                source=_format_sources(news, "东方财富资金流 / 新闻源"),
                category="capital_flow",
                importance="medium",
                summary=content,
                impact="结合行业资金流、板块涨跌和近期消息，仅作市场观察参考。",
                links=_format_links([item.get("link") for item in news[:5]]),
                market_scope="行业",
                related_sectors=[s["name"] for s in top_in[:3]],
            )
        )
    else:
        _send_health_status("DeepSeek 没有生成有效摘要")
