"""Explicitly non-production Yahoo Finance development probe.

This module is intentionally isolated from the radar's production providers.
It only fetches explicitly named symbols, prints an inspectable local report,
and never sends Telegram messages or creates tracking candidates.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Iterable, Optional

from config import settings
from utils.notifier import log_error, log_info


MAX_YFINANCE_DEV_TICKERS = 20
YFINANCE_BROAD_SCAN_RESULT_CAP = 250
TickerFactory = Callable[[str], Any]


def _as_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _read_fast_info(fast_info: Any, *keys: str) -> Optional[float]:
    for key in keys:
        try:
            value = fast_info.get(key)
        except AttributeError:
            try:
                value = fast_info[key]
            except (KeyError, TypeError):
                continue
        number = _as_number(value)
        if number is not None:
            return number
    return None


def _normalise_symbols(symbols: Iterable[Any]) -> list[str]:
    normalised: list[str] = []
    for raw_symbol in symbols:
        symbol = str(raw_symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
            log_error(f"⚠️ yfinance 开发测试忽略无效代码: {symbol or '空'}")
            continue
        if symbol not in normalised:
            normalised.append(symbol)
    return normalised


def _load_ticker_factory() -> TickerFactory:
    try:
        import yfinance
    except ImportError as exc:
        raise RuntimeError(
            "缺少开发测试依赖 yfinance；请安装 requirements-dev.txt"
        ) from exc
    return yfinance.Ticker


def _load_yfinance_module() -> Any:
    try:
        import yfinance
    except ImportError as exc:
        raise RuntimeError(
            "缺少开发测试依赖 yfinance；请安装 requirements-dev.txt"
        ) from exc
    return yfinance


def _read_mapping_value(item: Any, *keys: str) -> Optional[float]:
    for key in keys:
        try:
            value = item.get(key)
        except AttributeError:
            return None
        number = _as_number(value)
        if number is not None:
            return number
    return None


def _normalise_yfinance_screener_quote(item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    symbol = str(item.get("symbol") or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
        return None
    price = _read_mapping_value(item, "regularMarketPrice", "intradayprice", "price")
    if price is None or price <= 0:
        return None
    pct = _read_mapping_value(
        item, "regularMarketChangePercent", "percentchange", "changePercent"
    )
    volume = _read_mapping_value(item, "regularMarketVolume", "dayvolume", "volume")
    dollar_volume = price * volume if volume is not None else None
    if (
        pct is None
        or dollar_volume is None
        or not settings.US_RADAR_MIN_PRICE <= price <= settings.US_RADAR_MAX_PRICE
        or pct < settings.US_RADAR_MIN_DAY_CHANGE_PCT
        or dollar_volume < settings.US_RADAR_MIN_DOLLAR_VOLUME
    ):
        return None
    return {
        "symbol": symbol,
        "name": str(item.get("shortName") or item.get("longName") or symbol),
        "price": round(price, 4),
        "pct": round(pct, 4),
        "volume": int(volume),
        "dollar_volume": round(dollar_volume, 2),
        "source": "yfinance-experimental-screener",
    }


def fetch_yfinance_broad_market_candidates(
    yfinance_module: Optional[Any] = None,
) -> dict[str, Any]:
    """Run one capped Yahoo screener query over its US equity universe.

    This is a research-only, broad-market candidate query.  Yahoo caps the
    result page at 250 records, so it must never be presented as complete
    market coverage or a production real-time feed.
    """
    yfinance = yfinance_module or _load_yfinance_module()
    minimum_shares = math.ceil(
        settings.US_RADAR_MIN_DOLLAR_VOLUME / settings.US_RADAR_MAX_PRICE
    )
    query = yfinance.EquityQuery(
        "and",
        [
            yfinance.EquityQuery("eq", ["region", "us"]),
            yfinance.EquityQuery(
                "btwn",
                [
                    "intradayprice",
                    settings.US_RADAR_MIN_PRICE,
                    settings.US_RADAR_MAX_PRICE,
                ],
            ),
            yfinance.EquityQuery(
                "gte", ["percentchange", settings.US_RADAR_MIN_DAY_CHANGE_PCT]
            ),
            yfinance.EquityQuery("gte", ["dayvolume", minimum_shares]),
        ],
    )
    response = yfinance.screen(
        query,
        size=YFINANCE_BROAD_SCAN_RESULT_CAP,
        sortField="percentchange",
        sortAsc=False,
    )
    if not isinstance(response, dict):
        raise RuntimeError("Yahoo 市场筛选返回格式异常")
    raw_quotes = response.get("quotes", [])
    if not isinstance(raw_quotes, list):
        raise RuntimeError("Yahoo 市场筛选缺少 quotes 列表")
    candidates = [
        normalised
        for item in raw_quotes
        if (normalised := _normalise_yfinance_screener_quote(item)) is not None
    ]
    reported_total = _read_mapping_value(response, "total")
    return {
        "provider_reported_total": int(reported_total)
        if reported_total is not None
        else None,
        "result_cap": YFINANCE_BROAD_SCAN_RESULT_CAP,
        "returned_count": len(raw_quotes),
        "candidates": candidates,
    }


def fetch_yfinance_dev_quotes(
    symbols: Iterable[Any], ticker_factory: Optional[TickerFactory] = None
) -> list[dict[str, Any]]:
    """Fetch a small, named quote sample for development verification only."""
    clean_symbols = _normalise_symbols(symbols)
    if not clean_symbols:
        raise ValueError("请设置至少一个有效的 YFINANCE_DEV_TICKERS 标的")
    if len(clean_symbols) > MAX_YFINANCE_DEV_TICKERS:
        raise ValueError(
            f"开发测试最多允许 {MAX_YFINANCE_DEV_TICKERS} 个标的，避免高频请求"
        )

    create_ticker = ticker_factory or _load_ticker_factory()
    quotes: list[dict[str, Any]] = []
    for symbol in clean_symbols:
        try:
            fast_info = create_ticker(symbol).fast_info
            price = _read_fast_info(
                fast_info, "last_price", "lastPrice", "regularMarketPrice"
            )
            if price is None or price <= 0:
                raise ValueError("未获得有效最新价格")
            previous_close = _read_fast_info(
                fast_info, "previous_close", "previousClose", "regularMarketPreviousClose"
            )
            volume = _read_fast_info(
                fast_info, "last_volume", "lastVolume", "regularMarketVolume"
            )
            pct = (
                (price / previous_close - 1) * 100
                if previous_close is not None and previous_close > 0
                else None
            )
            estimated_dollar_volume = price * volume if volume is not None else None
            matches_radar_filters = (
                settings.US_RADAR_MIN_PRICE <= price <= settings.US_RADAR_MAX_PRICE
                and pct is not None
                and pct >= settings.US_RADAR_MIN_DAY_CHANGE_PCT
                and estimated_dollar_volume is not None
                and estimated_dollar_volume >= settings.US_RADAR_MIN_DOLLAR_VOLUME
            )
            quotes.append(
                {
                    "symbol": symbol,
                    "price": round(price, 4),
                    "previous_close": (
                        round(previous_close, 4) if previous_close is not None else None
                    ),
                    "pct": round(pct, 4) if pct is not None else None,
                    "reported_volume": int(volume) if volume is not None else None,
                    "estimated_dollar_volume": (
                        round(estimated_dollar_volume, 2)
                        if estimated_dollar_volume is not None
                        else None
                    ),
                    "matches_current_us_radar_filters": matches_radar_filters,
                    "source": "yfinance-development-only",
                }
            )
        except Exception as exc:
            log_error(
                f"⚠️ yfinance 开发测试获取失败 [{symbol}]: {exc.__class__.__name__}"
            )
    return quotes


def run_yfinance_dev_probe() -> None:
    """Print a local-only Yahoo development report; never send Telegram."""
    symbols = _normalise_symbols(settings.YFINANCE_DEV_TICKERS)
    if settings.YFINANCE_DEV_BROAD_SCAN:
        if symbols:
            raise RuntimeError(
                "广泛市场测试不应同时设置 YFINANCE_DEV_TICKERS；请只保留一种测试方式"
            )
        market_result = fetch_yfinance_broad_market_candidates()
        report = {
            "mode": "yfinance_dev",
            "purpose": "Yahoo 广泛市场开发测试；非生产行情源；不发送 Telegram",
            "coverage_warning": (
                "仅为 Yahoo 筛选器返回的候选页面，结果最多 250 条；"
                "不代表完整美国市场、实时确认或可交易性。"
            ),
            **market_result,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        log_info(
            "yfinance 广泛市场开发测试完成: "
            f"返回 {market_result['returned_count']} 条，"
            f"规则匹配 {len(market_result['candidates'])} 条"
        )
        return

    quotes = fetch_yfinance_dev_quotes(symbols)
    if not quotes:
        raise RuntimeError("yfinance 开发测试未获得任何有效报价，不能视为成功")

    requested = len(symbols)
    report = {
        "mode": "yfinance_dev",
        "purpose": "本地开发测试；非生产行情源；不发送 Telegram",
        "requested_symbols": symbols,
        "received_count": len(quotes),
        "failed_count": requested - len(quotes),
        "quotes": quotes,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    log_info(f"yfinance 开发测试完成: {len(quotes)}/{requested} 个标的有有效报价")
