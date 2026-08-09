"""Live, non-delivery probe for the required news-provider path."""

from __future__ import annotations

from config import settings
from core.data_fetcher import get_news, reset_data_source_health
from core.runtime import _record_news_summary
from utils.notifier import log_info


def run_source_canary() -> None:
    """Exercise the live news path and fail only when a core source is degraded."""
    reset_data_source_health()
    news = get_news(
        settings.MONITOR_NEWS_LOOKBACK_MINUTES,
        semantic_dedup=False,
        translate_external=False,
    )
    _record_news_summary(news)
    log_info(f"数据源烟囱检查完成: returned_news={len(news)}")
