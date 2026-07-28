from __future__ import annotations

import datetime
import email.utils
import json
import random
import re
import time
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Any, Optional
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

from config import settings
from core.models import NewsItem, validate_news_item
from utils.ai_client import get_ai_response
from utils.notifier import log_error, log_info
from utils.safety import redact_sensitive_text

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "policy": (
        "政策",
        "监管",
        "证监会",
        "央行",
        "财政部",
        "发改委",
        "国务院",
        "工信部",
        "商务部",
        "关税",
        "补贴",
        "地产政策",
        "降准",
        "降息",
    ),
    "capital_flow": (
        "资金流",
        "主力资金",
        "北向资金",
        "融资融券",
        "成交额",
        "放量",
        "缩量",
        "龙虎榜",
        "净流入",
        "净流出",
        "ETF",
        "基金",
    ),
    "company": (
        "财报",
        "业绩",
        "订单",
        "合同",
        "公告",
        "并购",
        "重组",
        "减持",
        "增持",
        "回购",
        "股东",
        "董事长",
        "CEO",
        "营收",
        "利润",
    ),
    "industry": (
        "半导体",
        "芯片",
        "AI",
        "人工智能",
        "算力",
        "机器人",
        "新能源",
        "光伏",
        "储能",
        "锂电",
        "医药",
        "创新药",
        "消费",
        "白酒",
        "地产",
        "银行",
        "券商",
        "保险",
        "军工",
        "汽车",
        "电力",
        "煤炭",
        "有色",
        "稀土",
    ),
    "market_sentiment": (
        "大涨",
        "大跌",
        "涨停",
        "跌停",
        "跳水",
        "拉升",
        "反弹",
        "杀跌",
        "恐慌",
        "避险",
        "风险偏好",
    ),
    "macro": (
        "CPI",
        "PPI",
        "PMI",
        "GDP",
        "通胀",
        "就业",
        "利率",
        "美联储",
        "美元",
        "人民币",
        "国债",
        "收益率",
        "原油",
        "黄金",
        "汇率",
    ),
    "overseas": (
        "Fed",
        "Federal Reserve",
        "Nasdaq",
        "S&P 500",
        "Dow",
        "Treasury",
        "yield",
        "oil",
        "gold",
        "Reuters",
        "Bloomberg",
        "ECB",
        "BOJ",
        "Europe",
        "US stocks",
        "美股",
        "港股",
        "海外",
        "全球",
    ),
}

SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "半导体": ("半导体", "芯片"),
    "AI": ("AI", "人工智能", "算力"),
    "机器人": ("机器人",),
    "新能源": ("新能源", "光伏", "储能", "锂电"),
    "医药": ("医药", "创新药"),
    "消费": ("消费", "白酒"),
    "地产": ("地产", "地产政策"),
    "金融": ("银行", "券商", "保险", "融资融券"),
    "军工": ("军工",),
    "汽车": ("汽车",),
    "电力": ("电力",),
    "资源": ("煤炭", "有色", "稀土", "原油", "黄金"),
}

HIGH_IMPORTANCE_KEYWORDS: tuple[str, ...] = (
    "国务院",
    "央行",
    "证监会",
    "财政部",
    "发改委",
    "美联储",
    "降准",
    "降息",
    "加息",
    "关税",
    "CPI",
    "PPI",
    "GDP",
    "PMI",
    "停牌",
    "复牌",
    "并购",
    "重组",
    "大跌",
    "跳水",
    "恐慌",
)

MEDIUM_IMPORTANCE_KEYWORDS: tuple[str, ...] = (
    "政策",
    "监管",
    "资金流",
    "北向资金",
    "主力资金",
    "净流入",
    "净流出",
    "业绩",
    "财报",
    "回购",
    "增持",
    "减持",
    "行业",
    "板块",
    "产业",
    "涨停",
    "跌停",
)
DATA_SOURCE_HEALTH: dict[str, dict[str, Any]] = {}


def _redact_sensitive_text(text: Any) -> str:
    """Return a short error detail without leaking configured secrets."""
    return redact_sensitive_text(text, max_length=120)


def reset_data_source_health() -> None:
    """Clear per-run data source health records."""
    DATA_SOURCE_HEALTH.clear()


def record_data_source_health(
    name: str, status: str, detail: Any = "", count: Optional[int] = None
) -> None:
    """Record one concise data source status for fallback health messages."""
    DATA_SOURCE_HEALTH[name] = {
        "status": status,
        "detail": _redact_sensitive_text(detail),
        "count": count,
    }


def get_data_source_health() -> dict[str, dict[str, Any]]:
    """Return a shallow copy of current data source health records."""
    return {name: dict(state) for name, state in DATA_SOURCE_HEALTH.items()}


def get_random_header() -> dict[str, str]:
    """生成随机请求头，伪装成浏览器。"""
    return {
        "User-Agent": random.choice(settings.USER_AGENTS),
        "Referer": "https://eastmoney.com/",
    }


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


def _strip_html(text: Any) -> str:
    """去除 HTML 标签并返回字符串。"""
    return re.sub(r"<[^>]+>", "", str(text or ""))


def _parse_datetime(raw_value: Any) -> Optional[datetime.datetime]:
    """解析常见日期格式并转换为上海时区。"""
    text = str(raw_value or "").strip()
    if not text:
        return None

    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None

    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=settings.SHA_TZ)
        return parsed.astimezone(settings.SHA_TZ)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return (
                dt.replace(tzinfo=settings.SHA_TZ)
                if dt.tzinfo is None
                else dt.astimezone(settings.SHA_TZ)
            )
        except ValueError:
            continue
    return None


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
            resp = requests.get(feed_url, headers=_rss_request_headers(), timeout=15)
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


def _combined_item_text(item: dict[str, Any]) -> str:
    """Return title/digest/source text for conservative rule matching."""
    return " ".join(
        str(item.get(key, "")) for key in ("title", "digest", "summary", "source")
    )


def _has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """Case-tolerant keyword check for mixed Chinese/English market text."""
    text_lower = text.lower()
    return any(keyword in text or keyword.lower() in text_lower for keyword in keywords)


def classify_news_item(item: dict[str, Any]) -> str:
    """Classify one news item with deterministic keyword rules."""
    text = _combined_item_text(item)
    source = str(item.get("source") or "").lower()

    if _has_keyword(text, CATEGORY_KEYWORDS["capital_flow"]):
        return "capital_flow"
    if _has_keyword(text, CATEGORY_KEYWORDS["policy"]):
        return "policy"
    if _has_keyword(text, CATEGORY_KEYWORDS["company"]):
        return "company"
    if _has_keyword(text, CATEGORY_KEYWORDS["industry"]):
        return "industry"
    if _has_keyword(text, CATEGORY_KEYWORDS["market_sentiment"]):
        return "market_sentiment"
    if _has_keyword(text, CATEGORY_KEYWORDS["overseas"]) or source not in (
        "",
        "eastmoney",
    ):
        return "overseas"
    if _has_keyword(text, CATEGORY_KEYWORDS["macro"]):
        return "macro"
    return "other"


def estimate_importance(item: dict[str, Any]) -> str:
    """Estimate information importance as high/medium/low without using AI."""
    text = _combined_item_text(item)
    category = str(item.get("category") or classify_news_item(item))

    if _has_keyword(text, HIGH_IMPORTANCE_KEYWORDS):
        return "high"
    if category in {"policy", "macro"}:
        return "high"
    if category in {"industry", "capital_flow", "overseas", "market_sentiment"}:
        return "medium"
    if _has_keyword(text, MEDIUM_IMPORTANCE_KEYWORDS):
        return "medium"
    return "low" if category == "company" else "medium"


def infer_market_scope(item: dict[str, Any]) -> str:
    """Infer the affected market scope, falling back to 其他 when uncertain."""
    text = _combined_item_text(item)
    source = str(item.get("source") or "").lower()
    category = str(item.get("category") or classify_news_item(item))

    if _has_keyword(text, ("A股", "沪深", "上证", "深成指", "创业板")):
        return "A股"
    if _has_keyword(text, ("港股", "恒生", "Hang Seng")):
        return "港股"
    if _has_keyword(text, ("美股", "Nasdaq", "S&P 500", "Dow", "US stocks")):
        return "美股"
    if category == "overseas" or source not in ("", "eastmoney"):
        return "全球"
    if category in {"industry", "capital_flow", "market_sentiment"}:
        return "行业"
    if category == "company":
        return "公司"
    if category in {"macro", "policy"}:
        return "A股"
    return "其他"


def infer_related_sectors(item: dict[str, Any]) -> list[str]:
    """Infer related sector labels from known keywords only."""
    text = _combined_item_text(item)
    sectors: list[str] = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        if _has_keyword(text, keywords):
            sectors.append(sector)
    return sectors


def _normalize_news_item(item: dict[str, Any]) -> NewsItem:
    """Add structured metadata while preserving existing news fields."""
    enriched = dict(item)
    enriched.setdefault("summary", str(enriched.get("digest") or "").strip())
    enriched.setdefault("url", str(enriched.get("link") or "").strip())

    news_time = enriched.get("datetime")
    if hasattr(news_time, "strftime"):
        enriched.setdefault("published_at", news_time.strftime("%Y-%m-%d %H:%M"))
    else:
        enriched.setdefault("published_at", str(enriched.get("time_str") or ""))

    enriched["category"] = str(enriched.get("category") or classify_news_item(enriched))
    enriched["importance"] = str(
        enriched.get("importance") or estimate_importance(enriched)
    )
    enriched["market_scope"] = str(
        enriched.get("market_scope") or infer_market_scope(enriched)
    )
    related = enriched.get("related_sectors")
    enriched["related_sectors"] = (
        related if isinstance(related, list) else infer_related_sectors(enriched)
    )
    return enriched  # type: ignore[return-value]


def enrich_news_items(news_items: list[dict[str, Any]]) -> list[NewsItem]:
    """Enrich a news list with category, importance, scope and sector tags."""
    enriched: list[NewsItem] = []
    for item in news_items:
        valid, reason = validate_news_item(item)
        if not valid:
            log_error(f"⚠️ 丢弃无效新闻记录: {reason}")
            continue
        enriched.append(_normalize_news_item(item))
    return enriched


def _extract_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    """从模型返回文本中提取第一个 JSON 对象。"""
    text = str(raw_text or "").strip()
    start_idx = text.find("{")
    if start_idx == -1:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[start_idx:])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_external_news(
    news_items: list[dict[str, Any]], max_translate_items: int = 20
) -> list[dict[str, Any]]:
    """使用 DeepSeek 批量判断语言并翻译非中文新闻。"""
    if not news_items:
        record_data_source_health("DeepSeek 翻译", "skipped", "无外部新闻", 0)
        return []

    candidate_indexes = [
        idx
        for idx, item in enumerate(news_items)
        if _needs_translation(item)
    ][:max_translate_items]
    if not candidate_indexes:
        record_data_source_health(
            "DeepSeek 翻译", "skipped", "本地检测均为中文", len(news_items)
        )
        return news_items

    prompt_rows = []
    for idx in candidate_indexes:
        item = news_items[idx]
        prompt_rows.append(
            {
                "idx": idx,
                "title": str(item.get("title", "")).strip(),
                "digest": str(item.get("digest", "")).strip(),
            }
        )

    prompt = (
        "请判断每条新闻是否为中文；若不是中文请翻译成简体中文。\n"
        "仅返回 JSON，格式如下："
        '{"items":[{"idx":0,"is_chinese":true,"title_zh":"...","digest_zh":"..."}]}\n\n'
        f"待处理列表：{json.dumps(prompt_rows, ensure_ascii=False)}"
    )

    ai_text = get_ai_response(prompt, temperature=0.0)
    parsed = _extract_json_object(ai_text or "")
    if not parsed or not isinstance(parsed.get("items"), list):
        record_data_source_health("DeepSeek 翻译", "failed", "返回格式异常", 0)
    else:
        record_data_source_health(
            "DeepSeek 翻译", "success", "", len(parsed.get("items", []))
        )
    mapped: dict[int, dict[str, Any]] = {}
    if parsed and isinstance(parsed.get("items"), list):
        for row in parsed["items"]:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row.get("idx"))
            except (TypeError, ValueError):
                continue
            mapped[idx] = row

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(news_items):
        if idx not in candidate_indexes:
            normalized.append(item)
            continue
        row = mapped.get(idx)
        if not row:
            normalized.append(item)
            continue

        if bool(row.get("is_chinese", False)):
            normalized.append(item)
            continue

        translated_item = dict(item)
        translated_item["title"] = str(
            row.get("title_zh") or item.get("title", "")
        ).strip()
        translated_item["digest"] = str(
            row.get("digest_zh") or item.get("digest", "")
        ).strip()
        translated_item["translated"] = True
        normalized.append(translated_item)

    return normalized


def _contains_chinese(text: Any) -> bool:
    """Detect Chinese locally so translation does not require an AI round trip."""
    return bool(re.search(r"[\u3400-\u9fff]", str(text or "")))


def _needs_translation(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "").strip()
    digest = str(item.get("digest") or "").strip()
    return (bool(title) and not _contains_chinese(title)) or (
        bool(digest) and not _contains_chinese(digest)
    )


def _normalized_title_for_similarity(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").lower()
    return re.sub(r"[\W_]+", "", title, flags=re.UNICODE)


def _semantic_duplicate_candidate_indexes(
    news_items: list[dict[str, Any]], threshold: float = 0.72
) -> set[int]:
    """Return only locally similar items that warrant AI semantic comparison."""
    normalized = [_normalized_title_for_similarity(item) for item in news_items]
    candidates: set[int] = set()
    for left_idx, left_title in enumerate(normalized):
        if len(left_title) < 8:
            continue
        for right_idx in range(left_idx + 1, len(normalized)):
            right_title = normalized[right_idx]
            if len(right_title) < 8:
                continue
            if SequenceMatcher(None, left_title, right_title).ratio() >= threshold:
                candidates.update((left_idx, right_idx))
    return candidates


def _refine_news(
    news_items: list[dict[str, Any]], max_items: int = 120
) -> list[dict[str, Any]]:
    """按标题去重并限制数量，输出更精简新闻流。"""
    refined: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in news_items:
        title = str(item.get("title", "")).strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        refined.append(item)
        if len(refined) >= max_items:
            break
    return refined


def _deduplicate_semantic_news(
    news_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """让 DeepSeek 通读新闻列表，删除含义相同的国内外新闻。"""
    if len(news_items) <= 1:
        record_data_source_health(
            "DeepSeek 去重", "skipped", "新闻数量不足", len(news_items)
        )
        return news_items

    candidate_indexes = _semantic_duplicate_candidate_indexes(news_items)
    if not candidate_indexes:
        record_data_source_health(
            "DeepSeek 去重", "skipped", "本地未发现疑似重复", len(news_items)
        )
        return news_items

    prompt_rows = []
    for idx in sorted(candidate_indexes):
        item = news_items[idx]
        prompt_rows.append(
            {
                "idx": idx,
                "source": str(item.get("source") or "unknown")[:40],
                "time": str(item.get("time_str") or "")[:20],
                "title": str(item.get("title") or "").strip()[:160],
                "digest": str(item.get("digest") or "").strip()[:220],
            }
        )

    prompt = (
        "请通读以下市场新闻列表，不区分海外新闻或国内新闻，删除含义相同、事实主体相同、"
        "只是来源/措辞/翻译不同的重复新闻。保留时间更新、信息量更完整或影响更直接的一条。"
        "不要删除只是同一主题但事实进展不同的新闻。\n"
        '仅返回 JSON，格式：{"keep":[0,2,5]}，keep 为需要保留的 idx，按原列表顺序排列。\n\n'
        f"待去重列表：{json.dumps(prompt_rows, ensure_ascii=False)}"
    )

    ai_text = get_ai_response(prompt, temperature=0.0)
    parsed = _extract_json_object(ai_text or "")
    raw_keep = parsed.get("keep") if parsed else None
    if not isinstance(raw_keep, list):
        record_data_source_health("DeepSeek 去重", "failed", "返回格式异常", 0)
        log_error("⚠️ DeepSeek 语义去重返回格式异常，使用标题去重结果")
        return news_items

    keep_indexes: list[int] = [
        idx for idx in range(len(news_items)) if idx not in candidate_indexes
    ]
    seen_indexes: set[int] = set()
    for raw_idx in raw_keep:
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        if idx in candidate_indexes and idx not in seen_indexes:
            seen_indexes.add(idx)
            keep_indexes.append(idx)

    if not seen_indexes:
        record_data_source_health("DeepSeek 去重", "failed", "未返回有效索引", 0)
        log_error("⚠️ DeepSeek 语义去重未返回有效索引，使用标题去重结果")
        return news_items

    keep_indexes.sort()
    record_data_source_health("DeepSeek 去重", "success", "", len(keep_indexes))
    if len(keep_indexes) < len(news_items):
        log_info(f"DeepSeek 语义去重：{len(news_items)} -> {len(keep_indexes)}")
    return [news_items[idx] for idx in keep_indexes]


def get_news(
    minutes_lookback: Optional[int] = None,
    *,
    semantic_dedup: bool = True,
    translate_external: bool = True,
) -> list[dict[str, Any]]:
    """
    抓取财经快讯。
    :param minutes_lookback: 回溯多少分钟内的新闻，None 表示 24 小时。
    :param semantic_dedup: 是否调用 AI 进行跨来源语义去重。
    :param translate_external: 是否调用 AI 翻译外部 RSS 新闻。
    """
    timestamp = int(time.time() * 1000)
    url = f"{settings.URL_NEWS}?_={timestamp}"

    try:
        resp = requests.get(url, headers=get_random_header(), timeout=15)
        log_info(f"东方财富快讯 HTTP 状态: status={resp.status_code}")
        payload = _extract_json_payload(resp.text.strip())
        valid_news: list[dict[str, Any]] = []

        if payload:
            items = payload.get("LivesList", [])
            if not isinstance(items, list):
                items = []
                reason = "LivesList 格式异常"
                record_data_source_health("东方财富快讯", "failed", reason, 0)
                log_error(f"东方财富快讯抓取失败: reason={reason}")
        else:
            items = []
            reason = "返回格式异常"
            record_data_source_health("东方财富快讯", "failed", reason, 0)
            log_error(f"东方财富快讯抓取失败: reason={reason}")

        raw_eastmoney_count = len(items)
        now = datetime.datetime.now(settings.SHA_TZ)
        delta = timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
        time_threshold = now - delta

        for item in items:
            if not isinstance(item, dict):
                continue

            show_time_str = item.get("showtime")
            try:
                news_time = datetime.datetime.strptime(
                    str(show_time_str), "%Y-%m-%d %H:%M:%S"
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

        if "东方财富快讯" not in DATA_SOURCE_HEALTH:
            record_data_source_health("东方财富快讯", "success", "", len(valid_news))
            log_info(
                f"东方财富快讯抓取成功: raw_count={raw_eastmoney_count}, "
                f"returned_count={len(valid_news)}"
            )
        else:
            log_error(
                f"东方财富快讯无可用数据: raw_count={raw_eastmoney_count}, "
                f"returned_count={len(valid_news)}"
            )

        external_news = _fetch_external_rss_news(minutes_lookback)
        normalized_external_news = (
            _normalize_external_news(external_news)
            if translate_external
            else external_news
        )

        merged_news = valid_news + normalized_external_news
        merged_news.sort(key=lambda x: x["datetime"], reverse=True)
        refined_news = _refine_news(merged_news)
        if semantic_dedup:
            refined_news = _deduplicate_semantic_news(refined_news)
        enriched_news = enrich_news_items(refined_news)
        log_info(
            f"新闻抓取汇总: eastmoney_count={len(valid_news)}, "
            f"rss_count={len(normalized_external_news)}, "
            f"merged_count={len(merged_news)}, final_count={len(enriched_news)}, "
            "fallback_used=false"
        )
        return enriched_news
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("东方财富快讯", "failed", reason, 0)
        log_error(f"❌ 东方财富快讯抓取失败: reason={reason}")
        log_info("新闻抓取 fallback: 使用 RSS 数据继续生成结果")
        external_news = _fetch_external_rss_news(minutes_lookback)
        normalized_external_news = (
            _normalize_external_news(external_news)
            if translate_external
            else external_news
        )
        normalized_external_news.sort(key=lambda x: x["datetime"], reverse=True)
        refined_news = _refine_news(normalized_external_news)
        if semantic_dedup:
            refined_news = _deduplicate_semantic_news(refined_news)
        enriched_news = enrich_news_items(refined_news)
        log_info(
            f"新闻抓取汇总: eastmoney_count=0, "
            f"rss_count={len(normalized_external_news)}, "
            f"merged_count={len(normalized_external_news)}, "
            f"final_count={len(enriched_news)}, "
            "fallback_used=true"
        )
        return enriched_news


def get_market_funds() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """抓取行业资金流向。"""
    params = {
        "pn": "1",
        "pz": "200",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": "m:90 t:2",
        "fields": "f12,f14,f2,f3,f62",
    }
    try:
        resp = requests.get(
            settings.URL_FUNDS, headers=get_random_header(), params=params, timeout=10
        )
        data = resp.json().get("data", {}).get("diff", [])
        if not isinstance(data, list):
            record_data_source_health("资金流数据", "failed", "返回格式异常", 0)
            return [], []

        sectors: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            flow = item.get("f62", 0) or 0
            try:
                flow_num = float(flow)
            except (ValueError, TypeError):
                flow_num = 0.0

            sector_name = item.get("f14", "未知")
            sectors.append(
                {
                    "name": sector_name,
                    "change": f"{item.get('f3', 0)}%",
                    "flow": round(flow_num / 100000000, 2),
                    "category": "capital_flow",
                    "importance": "medium",
                    "market_scope": "行业",
                    "related_sectors": [str(sector_name)],
                    "source": "eastmoney",
                }
            )

        sectors.sort(key=lambda x: x["flow"], reverse=True)
        record_data_source_health("资金流数据", "success", "", len(sectors))
        return sectors[:8], sectors[-8:]
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("资金流数据", "failed", reason, 0)
        log_error(f"❌ 资金流向获取失败: {reason}")
        return [], []


def get_hot_stocks_data() -> list[dict[str, Any]]:
    """抓取成交额前20的热门股。"""
    params = {
        "pn": "1",
        "pz": "20",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f6",
        "fs": "m:0+t:6,m:0+t:80",
        "fields": "f12,f14,f3,f6",
    }
    try:
        resp = requests.get(
            settings.URL_FUNDS, headers=get_random_header(), params=params, timeout=10
        )
        data = resp.json().get("data", {}).get("diff", [])
        if not isinstance(data, list):
            record_data_source_health("热门股数据", "failed", "返回格式异常", 0)
            return []

        stock_list: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                amount = float(item.get("f6", 0))
            except (ValueError, TypeError):
                amount = 0.0

            stock_list.append(
                {
                    "name": item.get("f14", "未知"),
                    "code": item.get("f12", ""),
                    "pct": f"{item.get('f3', '-')}%",
                    "amount": f"{round(amount / 100000000, 1)}亿",
                    "category": "company",
                    "importance": "medium",
                    "market_scope": "公司",
                    "related_sectors": [],
                    "source": "eastmoney",
                }
            )
        record_data_source_health("热门股数据", "success", "", len(stock_list))
        return stock_list
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("热门股数据", "failed", reason, 0)
        log_error(f"❌ 热门股获取失败: {reason}")
        return []


def _normalize_eastmoney_decimal(
    raw_value: Any, scale: int = 100, digits: int = 2
) -> str:
    """将东方财富常见的放大整数行情字段还原为小数文本。"""
    if raw_value in (None, "-", ""):
        return "-"

    try:
        value = float(raw_value)
        return f"{value / scale:.{digits}f}"
    except (ValueError, TypeError):
        return str(raw_value)


def get_stock_quote(code: Any) -> Optional[dict[str, str]]:
    """抓取单只股票行情。"""
    sec_id = f"1.{code}" if str(code).startswith("6") else f"0.{code}"
    url = f"{settings.URL_QUOTE}?secid={sec_id}&fields=f43,f170,f14"
    try:
        resp = requests.get(url, headers=get_random_header(), timeout=5)
        data = resp.json().get("data", {})
        if not data:
            record_data_source_health("个股行情", "failed", "返回空数据", 0)
            return None
        record_data_source_health("个股行情", "success", "", 1)
        return {
            "name": data.get("f14", "未知"),
            "price": _normalize_eastmoney_decimal(data.get("f43"), scale=100, digits=2),
            "pct": _normalize_eastmoney_decimal(data.get("f170"), scale=100, digits=2),
        }
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("个股行情", "failed", reason, 0)
        log_error(f"❌ 个股行情获取失败 [{code}]: {reason}")
        return None


def _as_positive_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalise_polygon_snapshot(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the small, provider-neutral quote shape used by the radar."""
    day = item.get("day") if isinstance(item.get("day"), dict) else {}
    last_trade = (
        item.get("lastTrade") if isinstance(item.get("lastTrade"), dict) else {}
    )
    price = _as_positive_float(last_trade.get("p")) or _as_positive_float(day.get("c"))
    symbol = str(item.get("ticker") or "").strip().upper()
    if not symbol or price is None:
        return None

    change_pct = item.get("todaysChangePerc")
    try:
        pct = float(change_pct)
    except (TypeError, ValueError):
        pct = 0.0
    try:
        volume = float(day.get("v") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    return {
        "symbol": symbol,
        "name": str(item.get("ticker") or symbol),
        "price": price,
        "pct": pct,
        "volume": volume,
        "dollar_volume": price * volume,
        "source": "polygon",
    }


def get_us_stock_snapshots() -> list[dict[str, Any]]:
    """Fetch US stock snapshots when Polygon is explicitly configured."""
    if not settings.POLYGON_API_KEY:
        return []
    try:
        response = requests.get(
            settings.URL_POLYGON_SNAPSHOTS,
            params={"apiKey": settings.POLYGON_API_KEY, "include_otc": "false"},
            timeout=15,
        )
        response.raise_for_status()
        raw_items = response.json().get("tickers", [])
        if not isinstance(raw_items, list):
            record_data_source_health("Polygon 美股行情", "failed", "返回格式异常", 0)
            return []
        snapshots = [
            normalised
            for item in raw_items
            if isinstance(item, dict)
            and (normalised := _normalise_polygon_snapshot(item)) is not None
        ]
        record_data_source_health("Polygon 美股行情", "success", "", len(snapshots))
        return snapshots
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("Polygon 美股行情", "failed", reason, 0)
        log_error(f"❌ Polygon 美股行情获取失败: {reason}")
        return []


def get_us_stock_quote(symbol: str) -> Optional[dict[str, Any]]:
    """Fetch one US stock snapshot for an active radar candidate."""
    clean_symbol = str(symbol or "").strip().upper()
    if not settings.POLYGON_API_KEY or not clean_symbol:
        return None
    try:
        response = requests.get(
            settings.URL_POLYGON_SINGLE_SNAPSHOT.format(symbol=clean_symbol),
            params={"apiKey": settings.POLYGON_API_KEY},
            timeout=10,
        )
        response.raise_for_status()
        raw_item = response.json().get("ticker")
        if not isinstance(raw_item, dict):
            record_data_source_health("Polygon 美股行情", "failed", "单标的返回为空", 0)
            return None
        quote = _normalise_polygon_snapshot(raw_item)
        if quote is None:
            record_data_source_health("Polygon 美股行情", "failed", "单标的字段不完整", 0)
            return None
        record_data_source_health("Polygon 美股行情", "success", "", 1)
        return quote
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("Polygon 美股行情", "failed", reason, 0)
        log_error(f"❌ Polygon 单标的行情获取失败 [{clean_symbol}]: {reason}")
        return None


def get_us_stock_news(symbol: str, limit: int = 2) -> list[dict[str, str]]:
    """Fetch recent source-attributed headlines for one US radar candidate."""
    clean_symbol = str(symbol or "").strip().upper()
    if not settings.POLYGON_API_KEY or not clean_symbol:
        return []
    try:
        response = requests.get(
            settings.URL_POLYGON_NEWS,
            params={
                "apiKey": settings.POLYGON_API_KEY,
                "ticker": clean_symbol,
                "limit": max(1, min(limit, 10)),
                "order": "desc",
                "sort": "published_utc",
            },
            timeout=10,
        )
        response.raise_for_status()
        raw_items = response.json().get("results", [])
        if not isinstance(raw_items, list):
            record_data_source_health("Polygon 美股新闻", "failed", "返回格式异常", 0)
            return []
        news: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            publisher = item.get("publisher") if isinstance(item.get("publisher"), dict) else {}
            news.append(
                {
                    "title": title,
                    "source": str(publisher.get("name") or "Polygon 新闻").strip(),
                    "link": str(item.get("article_url") or "").strip(),
                    "published_at": str(item.get("published_utc") or "").strip(),
                }
            )
        record_data_source_health("Polygon 美股新闻", "success", "", len(news))
        return news
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("Polygon 美股新闻", "failed", reason, 0)
        log_error(f"❌ Polygon 美股新闻获取失败 [{clean_symbol}]: {reason}")
        return []


def get_stock_history_closes(
    code: Any, start_date: str, max_sessions: int = 20
) -> list[dict[str, Any]]:
    """Return post-recommendation daily closes for fixed-horizon evaluation."""
    sec_id = f"1.{code}" if str(code).startswith("6") else f"0.{code}"
    try:
        start_day = datetime.datetime.strptime(str(start_date), "%Y-%m-%d")
    except ValueError:
        record_data_source_health("历史行情", "failed", "起始日期无效", 0)
        return []
    end_day = start_day + timedelta(days=max_sessions * 3 + 10)
    params = {
        "secid": sec_id,
        "klt": "101",
        "fqt": "1",
        "beg": start_day.strftime("%Y%m%d"),
        "end": end_day.strftime("%Y%m%d"),
        "lmt": str(max_sessions + 15),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    try:
        resp = requests.get(
            settings.URL_HISTORY,
            headers=get_random_header(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        klines = (resp.json().get("data") or {}).get("klines") or []
        closes: list[dict[str, Any]] = []
        for raw_row in klines:
            columns = str(raw_row or "").split(",")
            if len(columns) < 3 or columns[0] <= str(start_date):
                continue
            try:
                close = float(columns[2])
            except (TypeError, ValueError):
                continue
            closes.append({"date": columns[0], "close": close})
            if len(closes) >= max_sessions:
                break
        record_data_source_health("历史行情", "success", "", len(closes))
        return closes
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("历史行情", "failed", reason, 0)
        log_error(f"❌ 历史行情获取失败 [{code}]: {reason}")
        return []
