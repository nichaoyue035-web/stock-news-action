"""Global macro analyzer implementation."""

from __future__ import annotations

from datetime import datetime

from config import settings
from core.data_fetcher import get_news


def run_global(prompts: dict[str, str]) -> None:
    """Run global macro analysis mode."""
    from core.formatter import (
        _format_links,
        _format_market_message,
        _format_news_prompt_line,
        _format_sources,
    )
    from core.runtime import (
        _record_news_summary,
        _send_health_status,
        _send_tg_with_summary,
    )
    from core.analyzer import (
        _get_ai_response_with_health,
    )

    news = get_news(180)
    _record_news_summary(news)
    if not news:
        _send_health_status(
            "海外新闻数据为空，无法生成全球摘要",
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
        return
    news_txt = "\n".join(
        [
            f"{_format_news_prompt_line(n, include_time=False)} (详情:{n['digest'][:40]})"
            for n in news[:80]
        ]
    )
    content = _get_ai_response_with_health(
        prompts.get("global", settings.DEFAULT_PROMPTS["global"]).format(
            news_txt=news_txt
        )
    )
    if content and "无重大事件" not in content:
        now = datetime.now(settings.SHA_TZ)
        _send_tg_with_summary(
            _format_market_message(
                "国际宏观与板块雷达",
                report_time=now.strftime("%Y-%m-%d %H:%M"),
                source=_format_sources(news, "Reuters / RSS"),
                category="overseas",
                importance="medium",
                summary=content,
                impact="用于观察海外事件对全球市场、A股映射板块和风险偏好的可能影响。",
                links=_format_links([item.get("link") for item in news[:5]]),
                market_scope="全球",
                related_sectors=[
                    sector
                    for item in news[:20]
                    for sector in item.get("related_sectors", [])
                ][:6],
            ),
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
    else:
        _send_health_status(
            "DeepSeek 没有生成有效摘要或判断无重大事件",
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
            notify=False,
            severity="info",
        )
