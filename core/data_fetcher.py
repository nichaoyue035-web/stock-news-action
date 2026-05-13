from __future__ import annotations

import datetime
import email.utils
import json
import random
import re
import time
from datetime import timedelta
from typing import Any, Optional
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

from config import settings
from utils.ai_client import get_ai_response
from utils.notifier import log_error

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
    safe_text = str(text or "").replace("\n", " ").strip()
    for secret in (
        settings.DEEPSEEK_API_KEY,
        settings.TG_BOT_TOKEN,
        settings.TG_CHAT_ID,
        settings.TG_BOT_TOKEN_MONITOR,
        settings.TG_CHAT_ID_MONITOR,
    ):
        if secret:
            safe_text = safe_text.replace(str(secret), "<redacted>")
    return safe_text[:120] or "未知原因"


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


def _fetch_external_rss_news(
    minutes_lookback: Optional[int] = None,
) -> list[dict[str, Any]]:
    """从海外+自定义 RSS 信息源获取新闻。"""
    if not settings.EXTERNAL_NEWS_RSS:
        record_data_source_health("海外 RSS", "skipped", "未配置", 0)
        return []

    now = datetime.datetime.now(settings.SHA_TZ)
    delta = timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
    time_threshold = now - delta

    items: list[dict[str, Any]] = []
    failures: list[str] = []
    for feed_url in settings.EXTERNAL_NEWS_RSS:
        try:
            resp = requests.get(feed_url, headers=get_random_header(), timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as exc:
            reason = _redact_sensitive_text(exc)
            failures.append(reason)
            log_error(f"⚠️ 自定义信息源抓取失败 [{feed_url}]: {reason}")
            continue

        source_host = urlparse(feed_url).netloc or "custom"
        for node in root.findall(".//item") + root.findall(".//entry"):
            title = _strip_html(node.findtext("title"))
            digest = _strip_html(
                node.findtext("description") or node.findtext("summary")
            )
            link = node.findtext("link")
            if not link:
                link_node = node.find("link")
                link = link_node.get("href") if link_node is not None else None

            raw_time = (
                node.findtext("pubDate")
                or node.findtext("published")
                or node.findtext("updated")
                or node.findtext("dc:date")
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
    if failures and not items:
        record_data_source_health("海外 RSS", "failed", failures[0], 0)
    elif failures:
        record_data_source_health(
            "海外 RSS", "partial", f"部分失败：{failures[0]}", len(items)
        )
    else:
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


def _normalize_news_item(item: dict[str, Any]) -> dict[str, Any]:
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
    return enriched


def enrich_news_items(news_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich a news list with category, importance, scope and sector tags."""
    return [_normalize_news_item(item) for item in news_items]


def _extract_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    """从模型返回文本中提取 JSON 对象。"""
    match = re.search(r"\{[\s\S]*\}", str(raw_text or ""))
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_external_news(
    news_items: list[dict[str, Any]], max_translate_items: int = 20
) -> list[dict[str, Any]]:
    """使用 DeepSeek 批量判断语言并翻译非中文新闻。"""
    if not news_items:
        return []

    batch = news_items[:max_translate_items]
    prompt_rows = []
    for idx, item in enumerate(batch):
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
    for idx, item in enumerate(batch):
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

    return normalized + news_items[max_translate_items:]


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


def get_news(minutes_lookback: Optional[int] = None) -> list[dict[str, Any]]:
    """
    抓取财经快讯。
    :param minutes_lookback: 回溯多少分钟内的新闻，None 表示 24 小时。
    """
    timestamp = int(time.time() * 1000)
    url = f"{settings.URL_NEWS}?_={timestamp}"

    try:
        resp = requests.get(url, headers=get_random_header(), timeout=15)
        payload = _extract_json_payload(resp.text.strip())
        valid_news: list[dict[str, Any]] = []

        if payload:
            items = payload.get("LivesList", [])
            if not isinstance(items, list):
                items = []
                record_data_source_health(
                    "东方财富快讯", "failed", "LivesList 格式异常", 0
                )
        else:
            items = []
            record_data_source_health("东方财富快讯", "failed", "返回格式异常", 0)

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

        external_news = _fetch_external_rss_news(minutes_lookback)
        normalized_external_news = _normalize_external_news(external_news)

        merged_news = valid_news + normalized_external_news
        merged_news.sort(key=lambda x: x["datetime"], reverse=True)
        return enrich_news_items(_refine_news(merged_news))
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("东方财富快讯", "failed", reason, 0)
        log_error(f"❌ 新闻抓取失败: {reason}")
        external_news = _fetch_external_rss_news(minutes_lookback)
        normalized_external_news = _normalize_external_news(external_news)
        normalized_external_news.sort(key=lambda x: x["datetime"], reverse=True)
        return enrich_news_items(_refine_news(normalized_external_news))


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
