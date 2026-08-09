"""RSS and Atom source adapter with feed-level health reporting."""

from __future__ import annotations

import datetime
from datetime import timedelta
from typing import Any, Optional
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

from config import settings
from core.http_client import get_random_header, request_get
from core.news_source_common import _parse_datetime, _strip_html
from core.source_health import record_data_source_health, redact_error_detail
from utils.notifier import log_error, log_info


def _redact_sensitive_text(value: Any) -> str:
    return redact_error_detail(value)


def _rss_node_name(node: ET.Element) -> str:
    """Return XML node local name so RSS/Atom namespaces do not break parsing."""
    return str(node.tag).rsplit("}", 1)[-1].lower()


def _iter_rss_entries(root: ET.Element) -> list[ET.Element]:
    """Find RSS item and Atom entry nodes, including namespaced Atom feeds."""
    return [node for node in root.iter() if _rss_node_name(node) in {"item", "entry"}]


def _find_rss_child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    """Find the first child text by local name, ignoring namespaces."""
    expected = {name.lower() for name in names}
    for child in list(node):
        if _rss_node_name(child) in expected and child.text:
            return child.text
    return ""


def _find_rss_link(node: ET.Element, fallback: str) -> str:
    """Find RSS/Atom link text or href without inventing URLs."""
    for child in list(node):
        if _rss_node_name(child) != "link":
            continue
        href = child.get("href")
        if href:
            return href
        if child.text:
            return child.text
    return fallback


def _rss_request_headers() -> dict[str, str]:
    """Return browser-like headers for RSS sources that reject default clients."""
    headers = get_random_header()
    headers["Accept"] = (
        "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"
    )
    return headers


def _fetch_external_rss_news(
    minutes_lookback: Optional[int] = None,
) -> list[dict[str, Any]]:
    """从海外+自定义 RSS 信息源获取新闻，并输出逐源诊断日志。"""
    custom_count = len(settings.CUSTOM_NEWS_RSS)
    total_count = len(settings.EXTERNAL_NEWS_RSS)
    log_info(
        f"RSS 配置数量：GLOBAL={1 if settings.GLOBAL_NEWS_RSS else 0}, "
        f"CUSTOM={custom_count}, TOTAL={total_count}"
    )

    if not settings.EXTERNAL_NEWS_RSS:
        log_info("RSS URL empty, skipped")
        log_info("RSS 汇总：skipped, returned_count=0, reason=未配置")
        record_data_source_health("海外 RSS", "skipped", "未配置", 0)
        return []

    now = datetime.datetime.now(settings.SHA_TZ)
    delta = timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
    time_threshold = now - delta

    items: list[dict[str, Any]] = []
    failures: list[str] = []
    successful_feeds = 0

    for index, raw_feed_url in enumerate(settings.EXTERNAL_NEWS_RSS, start=1):
        feed_url = str(raw_feed_url or "").strip()
        if not feed_url:
            log_info("RSS URL empty, skipped")
            continue

        source_host = urlparse(feed_url).netloc or "custom"
        source_name = f"RSS {source_host}"
        log_info(f"RSS 抓取开始 ({index}/{total_count}): {feed_url}")

        try:
            resp = request_get(feed_url, headers=_rss_request_headers(), timeout=15)
            content_length = len(resp.content or b"")
            log_info(
                f"RSS HTTP 状态 [{feed_url}]: status={resp.status_code}, "
                f"length={content_length}"
            )

            if content_length == 0:
                reason = "empty response"
                failures.append(reason)
                record_data_source_health(source_name, "failed", reason, 0)
                log_error(f"⚠️ RSS 抓取失败 [{feed_url}]: {reason}")
                continue

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                reason = f"http error {resp.status_code}"
                failures.append(reason)
                record_data_source_health(source_name, "failed", reason, 0)
                log_error(
                    f"⚠️ RSS 抓取失败 [{feed_url}]: {reason} ({_redact_sensitive_text(exc)})"
                )
                continue

            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError as exc:
                reason = f"parse error: {_redact_sensitive_text(exc)}"
                failures.append(reason)
                record_data_source_health(source_name, "failed", reason, 0)
                log_error(f"⚠️ RSS 解析失败 [{feed_url}]: {reason}")
                continue

            nodes = _iter_rss_entries(root)
            successful_feeds += 1
            feed_item_count = 0

        except requests.Timeout:
            reason = "timeout"
            failures.append(reason)
            record_data_source_health(source_name, "failed", reason, 0)
            log_error(f"⚠️ RSS 抓取失败 [{feed_url}]: {reason}")
            continue
        except requests.RequestException as exc:
            reason = f"unknown error: {_redact_sensitive_text(exc)}"
            failures.append(reason)
            record_data_source_health(source_name, "failed", reason, 0)
            log_error(f"⚠️ RSS 抓取失败 [{feed_url}]: {reason}")
            continue
        except Exception as exc:
            reason = f"unknown error: {_redact_sensitive_text(exc)}"
            failures.append(reason)
            record_data_source_health(source_name, "failed", reason, 0)
            log_error(f"⚠️ RSS 抓取失败 [{feed_url}]: {reason}")
            continue

        for node in nodes:
            title = _strip_html(_find_rss_child_text(node, ("title",)))
            digest = _strip_html(_find_rss_child_text(node, ("description", "summary")))
            link = _find_rss_link(node, feed_url)
            raw_time = _find_rss_child_text(
                node, ("pubDate", "published", "updated", "date")
            )
            news_time = _parse_datetime(raw_time)
            if news_time is None or news_time < time_threshold:
                continue

            items.append(
                {
                    "title": title
                    or (digest[:50] + "..." if len(digest) > 50 else digest),
                    "digest": digest,
                    "link": link or feed_url,
                    "time_str": news_time.strftime("%H:%M"),
                    "datetime": news_time,
                    "source": source_host,
                }
            )
            feed_item_count += 1

        log_info(
            f"RSS 抓取成功 [{feed_url}]: entry_count={len(nodes)}, "
            f"returned_count={feed_item_count}"
        )
        record_data_source_health(source_name, "success", "", feed_item_count)

    if successful_feeds == 0 and failures:
        log_error(
            f"RSS 汇总：failed, returned_count={len(items)}, reason={failures[0]}"
        )
        record_data_source_health("海外 RSS", "failed", failures[0], len(items))
    elif failures:
        log_error(
            f"RSS 汇总：partial, successful_feeds={successful_feeds}, "
            f"returned_count={len(items)}, first_failure={failures[0]}"
        )
        record_data_source_health(
            "海外 RSS", "partial", f"部分失败：{failures[0]}", len(items)
        )
    else:
        log_info(f"RSS 汇总：success, returned_count={len(items)}")
        record_data_source_health("海外 RSS", "success", "", len(items))
    return items
