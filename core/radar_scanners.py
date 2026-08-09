"""Candidate-source scanners for the A-share and US market radar."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from config import settings
from core.radar_store import RadarStore
from utils.notifier import log_error, log_info


MAX_US_NEW_CANDIDATES_PER_RUN = 3

CreateCandidate = Callable[..., bool]
GetQuote = Callable[[str], Optional[dict[str, Any]]]
SessionCheck = Callable[[datetime], bool]
SafeFloat = Callable[[Any], Optional[float]]


def scan_a_share_candidates(
    store: RadarStore,
    now: datetime,
    *,
    normalise_codes: Callable[[list[str]], list[str]],
    is_trading_session: SessionCheck,
    get_stock_quote: GetQuote,
    safe_float: SafeFloat,
    create_candidate: CreateCandidate,
) -> tuple[int, int]:
    codes = normalise_codes(settings.RADAR_A_SHARE_CODES)
    if not codes or not is_trading_session(now):
        return 0, 0

    sampled = 0
    candidates = 0
    for code in codes:
        quote = get_stock_quote(code)
        if not quote:
            continue
        price = safe_float(quote.get("price"))
        pct = safe_float(quote.get("pct"))
        if price is None or price <= 0:
            log_info(f"雷达跳过无效 A 股价格: {code}")
            continue
        sampled += 1
        previous = store.record_quote(
            market="CN",
            symbol=code,
            name=str(quote.get("name") or code),
            price=price,
            pct=pct,
            volume=None,
            observed_at=now,
        )
        if not previous or float(previous["price"]) <= 0:
            continue
        change_pct = (price / float(previous["price"]) - 1) * 100
        if abs(change_pct) < settings.RADAR_A_SHARE_MINUTE_CHANGE_PCT:
            continue
        direction = "上涨" if change_pct > 0 else "下跌"
        evidence = f"最近可比较报价内变动 {change_pct:+.2f}%"
        if pct is not None:
            evidence += f"；当前当日涨跌 {pct:+.2f}%"
        if create_candidate(
            store,
            market="CN",
            symbol=code,
            name=str(quote.get("name") or code),
            price=price,
            pct=pct,
            volume=None,
            attributes={
                "signal": f"盘中快速{direction}",
                "minute_change_pct": round(change_pct, 2),
                "evidence": evidence,
                "catalyst": "本轮只确认了价格异动；尚未将单条新闻视为已确认催化。",
                "source": "eastmoney",
            },
            now=now,
        ):
            candidates += 1
    return sampled, candidates


def scan_a_share_hot_pool(
    store: RadarStore,
    now: datetime,
    *,
    is_trading_session: SessionCheck,
    get_hot_stocks_data: Callable[[], list[dict[str, Any]]],
    get_data_source_health: Callable[[], dict[str, dict[str, Any]]],
    safe_float: SafeFloat,
    create_candidate: CreateCandidate,
    set_run_reason: Callable[..., None],
) -> tuple[int, int]:
    """Use the high-turnover A-share pool as a coarse low-price scout."""
    if not settings.RADAR_A_SHARE_HOT_POOL_ENABLED or not is_trading_session(now):
        return 0, 0

    hot_stocks = get_hot_stocks_data()
    if not hot_stocks:
        health = get_data_source_health().get("热门股数据", {})
        if health.get("status") == "failed":
            set_run_reason("A 股热门池抓取失败", status="partial")
        return 0, 0

    eligible_stocks: list[tuple[dict[str, Any], float, float]] = []
    for item in hot_stocks:
        price = safe_float(item.get("price"))
        pct = safe_float(item.get("pct"))
        if price is None or pct is None:
            continue
        if not (
            settings.RADAR_A_SHARE_HOT_POOL_MIN_PRICE
            <= price
            <= settings.RADAR_A_SHARE_HOT_POOL_MAX_PRICE
            and settings.RADAR_A_SHARE_HOT_POOL_MIN_DAY_CHANGE_PCT
            <= pct
            <= settings.RADAR_A_SHARE_HOT_POOL_MAX_DAY_CHANGE_PCT
        ):
            continue
        eligible_stocks.append((item, price, pct))

    sampled = len(eligible_stocks)
    candidates = 0
    for item, price, pct in sorted(eligible_stocks, key=lambda entry: entry[2]):
        if candidates >= settings.RADAR_A_SHARE_HOT_POOL_MAX_NEW_CANDIDATES:
            continue
        raw_code = str(item.get("code") or "").strip()
        if not raw_code.isdigit():
            continue
        code = raw_code.zfill(6)
        if store.has_active_candidate("CN", code, now):
            continue
        if create_candidate(
            store,
            market="CN",
            symbol=code,
            name=str(item.get("name") or code),
            price=price,
            pct=pct,
            volume=None,
            attributes={
                "signal": "低价股早期走强",
                "evidence": (
                    f"成交额热门池中的低价股；股价 {price:.2f} 元，"
                    f"当日 {pct:+.2f}%。"
                ),
                "catalyst": "仅确认价格、涨幅和热门成交额排名；需自行核对公告、基本面与风险信息。",
                "source": "eastmoney-hot-pool",
            },
            now=now,
        ):
            candidates += 1
    return sampled, candidates


def scan_yahoo_experimental_candidates(
    store: RadarStore,
    now: datetime,
    *,
    is_trading_session: SessionCheck,
    fetch_candidates: Callable[[], dict[str, Any]],
    create_candidate: CreateCandidate,
    set_run_reason: Callable[..., None],
) -> tuple[int, int]:
    """Use Yahoo's capped screener as an explicitly experimental US scout."""
    if not settings.YFINANCE_EXPERIMENTAL_RADAR_ENABLED:
        return 0, 0
    if not is_trading_session(now):
        return 0, 0
    if now.minute % settings.YFINANCE_EXPERIMENTAL_RADAR_INTERVAL_MINUTES:
        return 0, 0

    try:
        result = fetch_candidates()
    except Exception as exc:
        set_run_reason(
            f"Yahoo 实验性候选池抓取失败: {exc.__class__.__name__}", status="partial"
        )
        log_error(f"❌ Yahoo 实验性候选池抓取失败: {exc.__class__.__name__}")
        return 0, 0

    candidates = 0
    for quote in result.get("candidates", []):
        if candidates >= settings.YFINANCE_EXPERIMENTAL_RADAR_MAX_NEW_CANDIDATES:
            break
        symbol = str(quote.get("symbol") or "")
        if not symbol or store.has_active_candidate("US", symbol, now):
            continue
        if create_candidate(
            store,
            market="US",
            symbol=symbol,
            name=str(quote.get("name") or symbol),
            price=float(quote["price"]),
            pct=float(quote["pct"]),
            volume=float(quote.get("volume") or 0),
            attributes={
                "signal": "低价股早期走强（实验性筛选）",
                "dollar_volume": float(quote["dollar_volume"]),
                "evidence": (
                    f"Yahoo 候选池：股价 ${float(quote['price']):.2f}；"
                    f"当日 {float(quote['pct']):+.2f}%；"
                    f"成交额约 ${float(quote['dollar_volume']) / 1_000_000:.1f}M。"
                ),
                "catalyst": "仅确认 Yahoo 的价格与成交字段；未把新闻标题视为已确认催化。",
                "source": "yfinance-experimental-screener",
            },
            now=now,
        ):
            candidates += 1
    return int(result.get("returned_count") or 0), candidates


def scan_us_candidates(
    store: RadarStore,
    now: datetime,
    *,
    is_trading_session: SessionCheck,
    scan_yahoo_candidates: Callable[[RadarStore, datetime], tuple[int, int]],
    get_us_stock_snapshots: Callable[[], list[dict[str, Any]]],
    get_us_stock_news: Callable[[str], list[dict[str, Any]]],
    create_candidate: CreateCandidate,
) -> tuple[int, int]:
    if not settings.POLYGON_API_KEY:
        return scan_yahoo_candidates(store, now)
    if not is_trading_session(now):
        return 0, 0
    snapshots = get_us_stock_snapshots()
    eligible = [
        quote
        for quote in snapshots
        if settings.US_RADAR_MIN_PRICE
        <= float(quote["price"])
        <= settings.US_RADAR_MAX_PRICE
        and settings.US_RADAR_MIN_DAY_CHANGE_PCT
        <= float(quote["pct"])
        <= settings.US_RADAR_MAX_DAY_CHANGE_PCT
        and float(quote["dollar_volume"]) >= settings.US_RADAR_MIN_DOLLAR_VOLUME
    ]
    eligible.sort(key=lambda quote: (float(quote["pct"]), -float(quote["dollar_volume"])))

    candidates = 0
    for quote in eligible:
        if candidates >= MAX_US_NEW_CANDIDATES_PER_RUN:
            break
        symbol = str(quote["symbol"])
        if store.has_active_candidate("US", symbol, now):
            continue
        headlines = get_us_stock_news(symbol)
        if headlines:
            headline = headlines[0]
            catalyst = f"[{headline['source']}] {headline['title']}"
        else:
            catalyst = "未获取到可核对的近期新闻；本次只作为高波动观察，不把价格上涨视为催化确认。"
        if create_candidate(
            store,
            market="US",
            symbol=symbol,
            name=str(quote.get("name") or symbol),
            price=float(quote["price"]),
            pct=float(quote["pct"]),
            volume=float(quote.get("volume") or 0),
            attributes={
                "signal": "低价股早期走强",
                "dollar_volume": float(quote["dollar_volume"]),
                "evidence": (
                    f"股价 ${float(quote['price']):.2f}；当日 {float(quote['pct']):+.2f}%；"
                    f"成交额约 ${float(quote['dollar_volume']) / 1_000_000:.1f}M。"
                ),
                "catalyst": catalyst,
                "source": str(quote.get("source") or "polygon"),
            },
            now=now,
        ):
            candidates += 1
    return len(snapshots), candidates
