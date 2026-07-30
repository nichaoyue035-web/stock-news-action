"""US-market premarket and intraday briefing implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import settings
from core.data_fetcher import get_news
from utils.notifier import log_info


US_RELEVANT_SCOPES = {"美股", "全球"}
US_RELEVANT_KEYWORDS = (
    "美股",
    "美国",
    "美联储",
    "纳斯达克",
    "标普",
    "道琼斯",
    "华尔街",
    "fed",
    "nasdaq",
    "s&p",
    "dow",
    "treasury",
    "us stocks",
)


def _is_us_market_relevant(item: dict[str, Any]) -> bool:
    """Keep US and global-market news, excluding A-share-only headlines."""
    scope = str(item.get("market_scope") or "").strip()
    if scope in US_RELEVANT_SCOPES:
        return True
    if str(item.get("category") or "").strip().lower() == "overseas":
        return True
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    return any(keyword in text for keyword in US_RELEVANT_KEYWORDS)


def _run_us_market_brief(prompts: dict[str, str], mode: str) -> None:
    """Build one US-market brief without inventing unavailable quote data."""
    from core.analyzer import _get_ai_response_with_health
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

    is_premarket = mode == "us_premarket"
    lookback_minutes = 720 if is_premarket else 240
    all_news = get_news(lookback_minutes)
    _record_news_summary(all_news)
    if not all_news:
        _send_health_status("新闻数据为空，无法生成美股市场简报")
        return

    news = [item for item in all_news if _is_us_market_relevant(item)]
    if not news:
        _set_run_reason("no US-relevant news in lookback", status="success")
        log_info("美股市场简报：未发现美股或全球联动新闻，跳过推送")
        return

    now = datetime.now(settings.US_EASTERN_TZ)
    news_txt = "\n".join(
        _format_news_prompt_line(item, include_time=True) for item in news[:25]
    )
    prompt_key = "us_premarket" if is_premarket else "us_periodic"
    content = _get_ai_response_with_health(
        prompts.get(prompt_key, settings.DEFAULT_PROMPTS[prompt_key]).format(
            news_txt=news_txt,
            report_date=now.strftime("%Y-%m-%d"),
            report_time=now.strftime("%Y-%m-%d %H:%M America/New_York"),
            report_weekday=_format_weekday(now),
        ),
        model="deepseek-reasoner" if is_premarket else "deepseek-chat",
    )
    if not content:
        _send_health_status("DeepSeek 没有生成有效美股市场摘要")
        return

    title = "美股盘前简报" if is_premarket else "美股盘中茶歇"
    fact_label = "盘前事实" if is_premarket else "盘中事实"
    importance = "中（盘前简报）" if is_premarket else "低（盘中简报）"
    _send_tg_with_summary(
        _format_market_message(
            title,
            report_time=now.strftime("%Y-%m-%d %H:%M America/New_York"),
            source=_format_sources(news, "海外 RSS / SEC"),
            category="market",
            importance=importance,
            summary=f"【{fact_label}】\n{_format_news_facts(news, limit=5)}",
            impact=content,
            links=_format_links([item.get("link") for item in news[:5]]),
            market_scope="美股 / 全球联动",
            related_sectors=[
                sector
                for item in news[:20]
                for sector in item.get("related_sectors", [])
            ][:6],
        )
    )


def run_us_premarket(prompts: dict[str, str]) -> None:
    """Send a US premarket brief using the prior twelve hours of news."""
    _run_us_market_brief(prompts, "us_premarket")


def run_us_periodic(prompts: dict[str, str]) -> None:
    """Send a US midday brief using the prior four hours of news."""
    _run_us_market_brief(prompts, "us_periodic")
