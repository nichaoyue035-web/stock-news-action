"""Global macro analyzer implementation."""

from __future__ import annotations

from datetime import datetime

from config import settings
from core.analyzers.monitor import is_three_hour_market_summary_item
from core.data_fetcher import get_data_source_health, get_news
from utils.notifier import log_info


def run_global(prompts: dict[str, str]) -> None:
    """Send a concise domestic-and-global market summary every three hours."""
    from core.formatter import (
        _format_links,
        _format_market_message,
        _format_news_prompt_line,
        _format_sources,
    )
    from core.runtime import (
        _record_news_summary,
        _record_fetch_success,
        _send_health_status,
        _send_tg_with_summary,
    )
    from core.analyzer import (
        _get_ai_response_with_health,
    )

    all_news = get_news(180)
    _record_news_summary(all_news)
    if not all_news:
        health = get_data_source_health()
        if any(state.get("status") == "failed" for state in health.values()):
            _send_health_status(
                "三小时市场总结的数据源未返回可用内容",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
        else:
            _record_fetch_success(True)
            log_info("三小时市场总结：近期没有可用新闻，跳过推送")
        return

    news = [item for item in all_news if is_three_hour_market_summary_item(item)]
    if not news:
        log_info("三小时市场总结：没有达到重要性阈值的市场变化，跳过推送")
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
    if not content:
        _send_health_status(
            "三小时市场总结的 AI 未生成有效内容",
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
        return
    if any(marker in content for marker in ("无重要市场变化", "无重大事件")):
        log_info("三小时市场总结：模型判断无重要市场变化，跳过推送")
        return

    now = datetime.now(settings.SHA_TZ)
    _send_tg_with_summary(
        _format_market_message(
            "三小时市场总结",
            report_time=now.strftime("%Y-%m-%d %H:%M"),
            source=_format_sources(news, "市场新闻源"),
            category="market",
            importance="medium",
            summary=content,
            impact="汇总过去三小时已发生的重要变化，供复核事实、传导路径和后续验证点。",
            links=_format_links([item.get("link") for item in news[:5]]),
            market_scope="国内外市场",
            related_sectors=[
                sector
                for item in news[:20]
                for sector in item.get("related_sectors", [])
            ][:6],
        ),
        token=settings.TG_BOT_TOKEN_MONITOR,
        chat_id=settings.TG_CHAT_ID_MONITOR,
    )
