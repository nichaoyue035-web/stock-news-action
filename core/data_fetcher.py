"""News orchestration facade preserving the project public API."""

from __future__ import annotations

from typing import Any, Optional

from config import settings as _settings
from core import (
    http_client,
    market_data,
    news_processing,
    news_sources,
    source_health,
)
from core.source_health import (
    record_data_source_health,
    redact_error_detail as _redact_sensitive_text,
)
from utils.notifier import log_error, log_info


# Compatibility attributes used by callers and existing test monkeypatches.
settings = _settings
requests = http_client.requests
time = http_client.time
_request_get = http_client.request_get
get_random_header = http_client.get_random_header
get_data_source_health = source_health.get_data_source_health
reset_data_source_health = source_health.reset_data_source_health

CATEGORY_KEYWORDS = news_processing.CATEGORY_KEYWORDS
SECTOR_KEYWORDS = news_processing.SECTOR_KEYWORDS
HIGH_IMPORTANCE_KEYWORDS = news_processing.HIGH_IMPORTANCE_KEYWORDS
MEDIUM_IMPORTANCE_KEYWORDS = news_processing.MEDIUM_IMPORTANCE_KEYWORDS
_combined_item_text = news_processing._combined_item_text
_has_keyword = news_processing._has_keyword
classify_news_item = news_processing.classify_news_item
estimate_importance = news_processing.estimate_importance
infer_market_scope = news_processing.infer_market_scope
infer_related_sectors = news_processing.infer_related_sectors
_normalize_news_item = news_processing._normalize_news_item
enrich_news_items = news_processing.enrich_news_items
_extract_json_object = news_processing._extract_json_object
_normalize_external_news = news_processing._normalize_external_news
_contains_chinese = news_processing._contains_chinese
_needs_translation = news_processing._needs_translation
_normalized_title_for_similarity = news_processing._normalized_title_for_similarity
_semantic_duplicate_candidate_indexes = news_processing._semantic_duplicate_candidate_indexes
_refine_news = news_processing._refine_news
_deduplicate_semantic_news = news_processing._deduplicate_semantic_news
get_ai_response = news_processing.get_ai_response

SEC_TICKERS_URL = news_sources.SEC_TICKERS_URL
SEC_SUBMISSIONS_URL = news_sources.SEC_SUBMISSIONS_URL
CSRC_NEWS_URL = news_sources.CSRC_NEWS_URL
SSE_ANNOUNCEMENTS_URL = news_sources.SSE_ANNOUNCEMENTS_URL
GDELT_DOC_API_URL = news_sources.GDELT_DOC_API_URL
CSRC_MATERIAL_TERMS = news_sources.CSRC_MATERIAL_TERMS
SSE_MATERIAL_TERMS = news_sources.SSE_MATERIAL_TERMS
_extract_json_payload = news_sources._extract_json_payload
_strip_html = news_sources._strip_html
_parse_datetime = news_sources._parse_datetime
_fetch_external_rss_news = news_sources._fetch_external_rss_news
_fetch_cn_official_news = news_sources._fetch_cn_official_news
_fetch_sec_edgar_filings = news_sources._fetch_sec_edgar_filings
_fetch_gdelt_discovery_news = news_sources._fetch_gdelt_discovery_news
_fetch_second_batch_news = news_sources._fetch_second_batch_news
_fetch_eastmoney_news = news_sources._fetch_eastmoney_news


def _run_news_stage(name: str, func, fallback):
    """Keep one optional news stage from discarding already fetched sources."""
    try:
        return func()
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health(name, "failed", reason, 0)
        log_error(f"❌ {name} 处理失败: reason={reason}")
        return fallback


def get_news(
    minutes_lookback: Optional[int] = None,
    *,
    semantic_dedup: bool = True,
    translate_external: bool = True,
) -> list[dict[str, Any]]:
    """Fetch, independently diagnose, and merge the configured news sources."""
    eastmoney_news = _fetch_eastmoney_news(minutes_lookback)
    external_news = _run_news_stage(
        "海外 RSS", lambda: _fetch_external_rss_news(minutes_lookback), []
    )
    normalized_external_news = (
        _run_news_stage(
            "DeepSeek 翻译",
            lambda: _normalize_external_news(external_news),
            external_news,
        )
        if translate_external
        else external_news
    )
    second_batch_news = _run_news_stage(
        "专用新闻源", lambda: _fetch_second_batch_news(minutes_lookback), []
    )

    merged_news = eastmoney_news + normalized_external_news + second_batch_news
    try:
        merged_news.sort(key=lambda item: item["datetime"], reverse=True)
        refined_news = _refine_news(merged_news)
        if semantic_dedup:
            refined_news = _deduplicate_semantic_news(refined_news)
        enriched_news = enrich_news_items(refined_news)
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("新闻合并处理", "failed", reason, 0)
        log_error(f"❌ 新闻合并处理失败: reason={reason}")
        enriched_news = enrich_news_items(
            [item for item in merged_news if isinstance(item, dict)]
        )

    log_info(
        f"新闻抓取汇总: eastmoney_count={len(eastmoney_news)}, "
        f"rss_count={len(normalized_external_news)}, "
        f"second_batch_count={len(second_batch_news)}, "
        f"merged_count={len(merged_news)}, final_count={len(enriched_news)}"
    )
    return enriched_news




# Compatibility exports: public market-data helpers keep their existing import path.
_as_positive_float = market_data._as_positive_float
_normalise_polygon_snapshot = market_data._normalise_polygon_snapshot
_normalize_eastmoney_decimal = market_data._normalize_eastmoney_decimal
get_hot_stocks_data = market_data.get_hot_stocks_data
get_market_funds = market_data.get_market_funds
get_stock_history_bars = market_data.get_stock_history_bars
get_stock_history_closes = market_data.get_stock_history_closes
get_stock_quote = market_data.get_stock_quote
get_us_stock_news = market_data.get_us_stock_news
get_us_stock_quote = market_data.get_us_stock_quote
get_us_stock_snapshots = market_data.get_us_stock_snapshots
