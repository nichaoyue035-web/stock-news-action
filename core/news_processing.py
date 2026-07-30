"""Deterministic enrichment and bounded AI refinement for market news."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Optional

from core.models import NewsItem, validate_news_item
from core.source_health import record_data_source_health
from utils.ai_client import get_ai_response
from utils.notifier import log_error, log_info


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
        idx for idx, item in enumerate(news_items) if _needs_translation(item)
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
