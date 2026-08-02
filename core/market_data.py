"""Market and quote provider adapters, independent from the news pipeline."""

from __future__ import annotations

import datetime
from datetime import timedelta
from typing import Any, Optional

from config import settings
from core.http_client import get_random_header, request_get
from core.source_health import record_data_source_health, redact_error_detail
from utils.notifier import log_error


def _redact_sensitive_text(value: Any) -> str:
    return redact_error_detail(value)


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
        resp = request_get(
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
        "fields": "f12,f14,f2,f3,f6",
    }
    try:
        resp = request_get(
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
                    "price": item.get("f2", "-"),
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
        resp = request_get(url, headers=get_random_header(), timeout=5)
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
        response = request_get(
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
        response = request_get(
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
        response = request_get(
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
        resp = request_get(
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


def get_stock_history_bars(
    code: Any, end_date: str, max_sessions: int = 65
) -> list[dict[str, Any]]:
    """Return recent adjusted A-share daily bars ending on ``end_date``.

    This is kept separate from ``get_stock_history_closes`` because review mode
    needs post-selection closes, while the medium-term selector needs bars
    leading up to one already-known close date.
    """
    try:
        end_day = datetime.datetime.strptime(str(end_date), "%Y-%m-%d")
    except ValueError:
        record_data_source_health("历史行情", "failed", "结束日期无效", 0)
        return []

    session_count = max(1, int(max_sessions))
    start_day = end_day - timedelta(days=session_count * 3 + 10)
    sec_id = f"1.{code}" if str(code).startswith("6") else f"0.{code}"
    params = {
        "secid": sec_id,
        "klt": "101",
        "fqt": "1",
        "beg": start_day.strftime("%Y%m%d"),
        "end": end_day.strftime("%Y%m%d"),
        "lmt": str(session_count + 20),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    try:
        resp = request_get(
            settings.URL_HISTORY,
            headers=get_random_header(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        klines = (resp.json().get("data") or {}).get("klines") or []
        bars: list[dict[str, Any]] = []
        for raw_row in klines:
            columns = str(raw_row or "").split(",")
            if len(columns) < 6 or columns[0] > str(end_date):
                continue
            try:
                close = float(columns[2])
                volume = float(columns[5])
            except (TypeError, ValueError):
                continue
            if close <= 0 or volume < 0:
                continue
            bars.append({"date": columns[0], "close": close, "volume": volume})
        bars.sort(key=lambda item: str(item["date"]))
        recent_bars = bars[-session_count:]
        record_data_source_health("历史行情", "success", "", len(recent_bars))
        return recent_bars
    except Exception as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health("历史行情", "failed", reason, 0)
        log_error(f"❌ 历史行情获取失败 [{code}]: {reason}")
        return []
