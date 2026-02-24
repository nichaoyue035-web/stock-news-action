from __future__ import annotations

import datetime
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import timedelta
from typing import Any, Optional

import requests

from config import settings
from utils.ai_client import get_ai_response
from utils.notifier import log_error


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


def _contains_chinese(text: str) -> bool:
    """判断文本是否包含中文。"""
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _translate_to_chinese(text: str) -> str:
    """使用 DeepSeek 将外语文本翻译为中文；失败时回退原文。"""
    cleaned = str(text or "").strip()
    if not cleaned or _contains_chinese(cleaned):
        return cleaned

    prompt = (
        "请把下面这段财经新闻文本翻译成简体中文，只输出翻译结果，不要解释：\n\n"
        f"{cleaned}"
    )
    translated = get_ai_response(prompt, temperature=0.0)
    if not translated:
        return cleaned
    return translated.strip()


def _parse_news_items(items: list[Any], time_threshold: datetime.datetime) -> list[dict[str, Any]]:
    """将东财新闻列表解析为统一结构。"""
    valid_news: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        show_time_str = item.get("showtime")
        try:
            news_time = datetime.datetime.strptime(str(show_time_str), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=settings.SHA_TZ
            )
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
            }
        )
    return valid_news


def _fetch_news_from_source(url_base: str, time_threshold: datetime.datetime) -> list[dict[str, Any]]:
    """从单一快讯源抓取并解析新闻。"""
    timestamp = int(time.time() * 1000)
    url = f"{url_base}?_={timestamp}"
    resp = requests.get(url, headers=get_random_header(), timeout=15)
    payload = _extract_json_payload(resp.text.strip())
    if not payload:
        return []

    items = payload.get("LivesList", [])
    if not isinstance(items, list):
        return []

    return _parse_news_items(items, time_threshold)


def _fetch_rss_news_from_source(url: str, time_threshold: datetime.datetime) -> list[dict[str, Any]]:
    """从 RSS 源抓取并转换为统一新闻结构。"""
    resp = requests.get(url, headers=get_random_header(), timeout=15)
    root = ET.fromstring(resp.text)

    news: list[dict[str, Any]] = []
    now = datetime.datetime.now(settings.SHA_TZ)
    for item in root.findall(".//item"):
        title = _strip_html((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip() or "https://finance.ifeng.com/"
        digest = _strip_html((item.findtext("description") or "").strip())
        pub_date = (item.findtext("pubDate") or "").strip()

        news_time = now
        if pub_date:
            try:
                news_time = datetime.datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z").astimezone(
                    settings.SHA_TZ
                )
            except ValueError:
                pass

        if news_time < time_threshold:
            continue

        if len(title) < 5:
            title = digest[:50] + "..." if len(digest) > 50 else digest

        title_zh = _translate_to_chinese(title)
        digest_zh = _translate_to_chinese(digest)

        news.append(
            {
                "title": title_zh,
                "digest": digest_zh,
                "link": link,
                "time_str": news_time.strftime("%H:%M"),
                "datetime": news_time,
            }
        )
    return news


def get_news(minutes_lookback: Optional[int] = None) -> list[dict[str, Any]]:
    """
    抓取财经快讯。
    :param minutes_lookback: 回溯多少分钟内的新闻，None 表示 24 小时。
    """
    try:
        now = datetime.datetime.now(settings.SHA_TZ)
        delta = timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
        time_threshold = now - delta

        primary_news = _fetch_news_from_source(settings.URL_NEWS, time_threshold)
        alt_source = getattr(settings, "URL_NEWS_ALT", "")
        intl_source = getattr(settings, "URL_NEWS_INTL", "")
        alt_news: list[dict[str, Any]] = []
        intl_news: list[dict[str, Any]] = []
        if alt_source:
            if "eastmoney" in alt_source:
                alt_news = _fetch_news_from_source(alt_source, time_threshold)
            else:
                alt_news = _fetch_rss_news_from_source(alt_source, time_threshold)
        if intl_source:
            intl_news = _fetch_rss_news_from_source(intl_source, time_threshold)

        merged = primary_news + alt_news + intl_news
        if not merged:
            return []

        dedup: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in merged:
            dedup_key = (item.get("title", ""), item.get("time_str", ""))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            dedup.append(item)

        dedup.sort(key=lambda item: item.get("datetime", now), reverse=True)
        return dedup
    except Exception as exc:
        log_error(f"❌ 新闻抓取失败: {exc}")
        return []


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
        resp = requests.get(settings.URL_FUNDS, headers=get_random_header(), params=params, timeout=10)
        data = resp.json().get("data", {}).get("diff", [])
        if not isinstance(data, list):
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

            sectors.append(
                {
                    "name": item.get("f14", "未知"),
                    "change": f"{item.get('f3', 0)}%",
                    "flow": round(flow_num / 100000000, 2),
                }
            )

        sectors.sort(key=lambda x: x["flow"], reverse=True)
        return sectors[:8], sectors[-8:]
    except Exception as exc:
        log_error(f"❌ 资金流向获取失败: {exc}")
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
        resp = requests.get(settings.URL_FUNDS, headers=get_random_header(), params=params, timeout=10)
        data = resp.json().get("data", {}).get("diff", [])
        if not isinstance(data, list):
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
                }
            )
        return stock_list
    except Exception as exc:
        log_error(f"❌ 热门股获取失败: {exc}")
        return []


def _normalize_eastmoney_decimal(raw_value: Any, scale: int = 100, digits: int = 2) -> str:
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
            return None
        return {
            "name": data.get("f14", "未知"),
            "price": _normalize_eastmoney_decimal(data.get("f43"), scale=100, digits=2),
            "pct": _normalize_eastmoney_decimal(data.get("f170"), scale=100, digits=2),
        }
    except Exception as exc:
        log_error(f"❌ 个股行情获取失败 [{code}]: {exc}")
        return None
