"""Eastmoney flash-news source adapter."""

from __future__ import annotations

import datetime
import json
import time
from datetime import timedelta
from typing import Any, Optional

from config import settings
from core.http_client import get_random_header, request_get
from core.news_source_common import _strip_html
from core.source_health import record_data_source_health, redact_error_detail
from utils.notifier import log_error, log_info


def _redact_sensitive_text(value: Any) -> str:
    return redact_error_detail(value)


def _extract_json_payload(raw_content: str) -> Optional[dict[str, Any]]:
    """从东方财富返回文本中提取 JSON 对象。"""
    start_idx = raw_content.find("{")
    end_idx = raw_content.rfind("}")
    if start_idx == -1 or end_idx == -1:
        return None

    try:
        payload = json.loads(raw_content[start_idx : end_idx + 1])
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


def _fetch_eastmoney_news(
    minutes_lookback: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Fetch Eastmoney independently so downstream failures cannot mislabel it."""
    timestamp = int(time.time() * 1000)
    url = f"{settings.URL_NEWS}?_={timestamp}"
    try:
        response = request_get(url, headers=get_random_header(), timeout=15)
        log_info(
            f"东方财富快讯 HTTP 状态: status={getattr(response, 'status_code', '未知')}"
        )
        payload = _extract_json_payload(str(response.text or "").strip())
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("东方财富快讯", "failed", reason, 0)
        log_error(f"❌ 东方财富快讯抓取失败: reason={reason}")
        return []

    raw_items = payload.get("LivesList", []) if payload else []
    if not isinstance(raw_items, list):
        raw_items = []
    if payload is None or not isinstance(payload.get("LivesList", []), list):
        reason = "返回格式异常" if payload is None else "LivesList 格式异常"
        record_data_source_health("东方财富快讯", "failed", reason, 0)
        log_error(f"东方财富快讯抓取失败: reason={reason}")
        return []

    now = datetime.datetime.now(settings.SHA_TZ)
    delta = timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
    time_threshold = now - delta
    valid_news: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            news_time = datetime.datetime.strptime(
                str(item.get("showtime")), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=settings.SHA_TZ)
        except ValueError:
            continue
        if news_time < time_threshold:
            continue

        digest = str(item.get("digest", ""))
        title = str(item.get("title", ""))
        if len(title) < 5:
            title = digest[:50] + "..." if len(digest) > 50 else digest
        valid_news.append(
            {
                "title": _strip_html(title),
                "digest": _strip_html(digest),
                "link": item.get("url_unique") or "https://kuaixun.eastmoney.com/",
                "time_str": news_time.strftime("%H:%M"),
                "datetime": news_time,
                "source": "eastmoney",
            }
        )

    record_data_source_health("东方财富快讯", "success", "", len(valid_news))
    log_info(
        f"东方财富快讯抓取成功: raw_count={len(raw_items)}, "
        f"returned_count={len(valid_news)}"
    )
    return valid_news
