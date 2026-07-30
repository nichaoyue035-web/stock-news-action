"""Event-driven candidate tracking for A-share and US-stock market data."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional

from config import settings
from core.data_fetcher import (
    get_data_source_health,
    get_hot_stocks_data,
    get_stock_quote,
    get_us_stock_news,
    get_us_stock_quote,
    get_us_stock_snapshots,
)
from core.interaction_auth import is_authorized_interaction
from core.radar_store import RadarStore
from core.runtime import (
    _record_fetch_success,
    _set_run_reason,
    _set_run_summary,
    _send_tg_with_summary,
)
from core.yfinance_dev import (
    fetch_yfinance_broad_market_candidates,
    fetch_yfinance_dev_quotes,
)
from utils.notifier import log_error, log_info, send_tg_interactive


RADAR_CALLBACK_PREFIX = "radar"
MAX_US_NEW_CANDIDATES_PER_RUN = 3


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _short_text(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _is_experimental_yahoo_source(attributes: dict[str, Any]) -> bool:
    return str(attributes.get("source") or "").startswith("yfinance-")


def _normalise_a_share_codes(raw_codes: list[str]) -> list[str]:
    codes: list[str] = []
    for raw_code in raw_codes:
        code = str(raw_code or "").strip()
        if not code.isdigit() or len(code) > 6:
            log_info(f"雷达忽略无效 A 股代码: {code or '空'}")
            continue
        code = code.zfill(6)
        if code not in codes:
            codes.append(code)
    return codes


def _is_a_share_trading_session(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    current_time = now.astimezone(settings.SHA_TZ).time().replace(tzinfo=None)
    return time(9, 30) <= current_time <= time(11, 30) or time(13, 0) <= current_time <= time(15, 0)


def _is_us_trading_session(now: datetime) -> bool:
    """Cover US pre-market, regular session and after-hours in Eastern Time."""
    local = now.astimezone(settings.US_EASTERN_TZ)
    if local.weekday() >= 5:
        return False
    current_time = local.time().replace(tzinfo=None)
    return time(4, 0) <= current_time <= time(20, 0)


def _market_label(market: str) -> str:
    return "A股" if market == "CN" else "美股"


def _market_close_minutes(market: str, now: datetime) -> int:
    timezone = settings.SHA_TZ if market == "CN" else settings.US_EASTERN_TZ
    local_now = now.astimezone(timezone)
    close_time = time(15, 0) if market == "CN" else time(16, 0)
    close_at = local_now.replace(
        hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0
    )
    if close_at <= local_now:
        return 1
    return max(1, int((close_at - local_now).total_seconds() // 60))


def _signal_text(attributes: dict[str, Any]) -> str:
    signal = str(attributes.get("signal") or "价格异动")
    if signal == "盘中快速上涨":
        return "价格短时上涨，已自动进入短时追踪。"
    if signal == "盘中快速下跌":
        return "价格短时下跌，已自动进入风险追踪。"
    return "价格、成交与筛选条件已触发，已自动进入短时追踪。"


def _candidate_buttons(candidate_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "继续追踪 2 小时",
                    "callback_data": f"{RADAR_CALLBACK_PREFIX}:{candidate_id}:120",
                },
                {
                    "text": "跟踪至收盘",
                    "callback_data": f"{RADAR_CALLBACK_PREFIX}:{candidate_id}:close",
                },
            ],
            [
                {
                    "text": "停止追踪",
                    "callback_data": f"{RADAR_CALLBACK_PREFIX}:{candidate_id}:stop",
                }
            ],
        ]
    }


def _format_candidate_message(candidate: dict[str, Any]) -> str:
    attributes = candidate["attributes"]
    price = float(candidate["initial_price"])
    pct = candidate.get("initial_pct")
    pct_text = f"{float(pct):+.2f}%" if pct is not None else "未知"
    volume = attributes.get("dollar_volume")
    volume_text = (
        f"${float(volume) / 1_000_000:.1f}M" if isinstance(volume, (float, int)) else "待补充"
    )
    evidence = _short_text(attributes.get("evidence") or "行情数据触发")
    catalyst = _short_text(
        attributes.get("catalyst") or "暂未核对到可用的新闻催化。"
    )
    data_limit = (
        "Yahoo 实验性候选池，可能延迟、遗漏或限流；仅作线索，不代表完整市场。"
        if _is_experimental_yahoo_source(attributes)
        else ""
    )
    lines = [
        f"🟡 实时标的雷达｜{_market_label(candidate['market'])}｜自动追踪中",
        f"标的：{candidate['symbol']} {candidate['name']}",
        f"触发价：{price:.2f}｜当日涨跌：{pct_text}",
        "",
        "【触发事实】",
        evidence,
        f"成交额：{volume_text}",
        "",
        "【当前操作状态】",
        "暂不按初始异动处理；系统将自动核对成交持续性与价格是否失效。",
        "",
        "【催化核对】",
        catalyst,
        *(["", "【数据限制】", data_limit] if data_limit else []),
        "",
        "【确认条件】",
        "短时价格未明显回落，且下一轮成交/行情数据仍可核对。",
        "",
        "【失效／回避条件】",
        f"触发价回落 {settings.RADAR_INVALIDATION_PCT:.1f}% 以上、行情数据失真或出现风险信息时停止追踪。",
        "",
        f"说明：{_signal_text(attributes)} 仅作信息观察，不构成买卖建议。",
    ]
    return "\n".join(lines)


def _format_update_message(candidate: dict[str, Any], state: str) -> str:
    initial_price = float(candidate["initial_price"])
    last_price = candidate.get("last_price")
    if last_price is None:
        last_text = "暂无有效新报价"
        change_text = "未知"
    else:
        last = float(last_price)
        last_text = f"{last:.2f}"
        change_text = f"{(last / initial_price - 1) * 100:+.2f}%"
    title = {
        "confirmed": "🟢 实时标的雷达｜条件暂未失效",
        "invalidated": "🔴 实时标的雷达｜信号已失效",
        "expired": "⚪️ 实时标的雷达｜自动追踪结束",
    }[state]
    status_text = {
        "confirmed": "初始追踪窗口内未出现设定的回落失效条件，继续观察但不把它视为买卖指令。",
        "invalidated": "价格已触及预设失效条件，停止按这次初始异动继续关注。",
        "expired": "本轮自动追踪已到期；如仍需要，点击原消息按钮可在到期前延长追踪。",
    }[state]
    return "\n".join(
        (
            title,
            f"标的：{candidate['symbol']} {candidate['name']}｜{_market_label(candidate['market'])}",
            f"触发价：{initial_price:.2f}｜最新价：{last_text}｜相对触发：{change_text}",
            "",
            "【操作状态】",
            status_text,
            "",
            "【风险提示】",
            "行情可能延迟、跳空或快速反转；请自行核对最新报价与原始公告。",
        )
    )


def _send_candidate(candidate: dict[str, Any], store: RadarStore, now: datetime) -> bool:
    _set_run_summary(telegram_attempted=True)
    message_id = send_tg_interactive(
        _format_candidate_message(candidate),
        reply_markup=_candidate_buttons(str(candidate["candidate_id"])),
        token=settings.INTERACTION_BOT_TOKEN,
        chat_id=settings.INTERACTION_CHAT_ID,
    )
    if message_id is None:
        _set_run_summary(telegram_sent=False, status="failed")
        return False
    store.set_telegram_delivery(
        str(candidate["candidate_id"]),
        str(settings.INTERACTION_CHAT_ID),
        message_id,
        now,
    )
    _set_run_summary(telegram_sent=True)
    return True


def _create_candidate(
    store: RadarStore,
    *,
    market: str,
    symbol: str,
    name: str,
    price: float,
    pct: Optional[float],
    volume: Optional[float],
    attributes: dict[str, Any],
    now: datetime,
) -> bool:
    candidate, created = store.create_candidate(
        market=market,
        symbol=symbol,
        name=name,
        price=price,
        pct=pct,
        volume=volume,
        attributes=attributes,
        now=now,
        initial_track_minutes=settings.RADAR_INITIAL_TRACK_MINUTES,
    )
    if not created:
        return False
    if not _send_candidate(candidate, store, now):
        store.close_candidate(str(candidate["candidate_id"]), "Telegram 初始推送失败", now)
        return False
    return True


def _scan_a_share_candidates(store: RadarStore, now: datetime) -> tuple[int, int]:
    codes = _normalise_a_share_codes(settings.RADAR_A_SHARE_CODES)
    if not codes:
        return 0, 0
    if not _is_a_share_trading_session(now):
        return 0, 0

    sampled = 0
    candidates = 0
    for code in codes:
        quote = get_stock_quote(code)
        if not quote:
            continue
        price = _safe_float(quote.get("price"))
        pct = _safe_float(quote.get("pct"))
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
        if _create_candidate(
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


def _scan_a_share_hot_pool(store: RadarStore, now: datetime) -> tuple[int, int]:
    """Use the high-turnover A-share pool as a coarse low-price scout."""
    if not settings.RADAR_A_SHARE_HOT_POOL_ENABLED:
        return 0, 0
    if not _is_a_share_trading_session(now):
        return 0, 0

    hot_stocks = get_hot_stocks_data()
    if not hot_stocks:
        health = get_data_source_health().get("热门股数据", {})
        if health.get("status") == "failed":
            _set_run_reason("A 股热门池抓取失败", status="partial")
        return 0, 0

    sampled = 0
    candidates = 0
    for item in hot_stocks:
        price = _safe_float(item.get("price"))
        pct = _safe_float(item.get("pct"))
        if price is None or pct is None:
            continue
        if not (
            settings.RADAR_A_SHARE_HOT_POOL_MIN_PRICE
            <= price
            <= settings.RADAR_A_SHARE_HOT_POOL_MAX_PRICE
            and pct >= settings.RADAR_A_SHARE_HOT_POOL_MIN_DAY_CHANGE_PCT
        ):
            continue
        sampled += 1
        if candidates >= settings.RADAR_A_SHARE_HOT_POOL_MAX_NEW_CANDIDATES:
            continue
        raw_code = str(item.get("code") or "").strip()
        if not raw_code.isdigit():
            continue
        code = raw_code.zfill(6)
        if store.has_active_candidate("CN", code, now):
            continue
        if _create_candidate(
            store,
            market="CN",
            symbol=code,
            name=str(item.get("name") or code),
            price=price,
            pct=pct,
            volume=None,
            attributes={
                "signal": "低价高换手上涨",
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


def _scan_yahoo_experimental_candidates(
    store: RadarStore, now: datetime
) -> tuple[int, int]:
    """Use Yahoo's capped screener as an explicitly experimental US scout."""
    if not settings.YFINANCE_EXPERIMENTAL_RADAR_ENABLED:
        return 0, 0
    if not _is_us_trading_session(now):
        return 0, 0
    if now.minute % settings.YFINANCE_EXPERIMENTAL_RADAR_INTERVAL_MINUTES:
        return 0, 0

    try:
        result = fetch_yfinance_broad_market_candidates()
    except Exception as exc:
        _set_run_reason(
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
        if _create_candidate(
            store,
            market="US",
            symbol=symbol,
            name=str(quote.get("name") or symbol),
            price=float(quote["price"]),
            pct=float(quote["pct"]),
            volume=float(quote.get("volume") or 0),
            attributes={
                "signal": "低价股放量上涨（实验性筛选）",
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


def _scan_us_candidates(store: RadarStore, now: datetime) -> tuple[int, int]:
    if not settings.POLYGON_API_KEY:
        return _scan_yahoo_experimental_candidates(store, now)
    if not _is_us_trading_session(now):
        return 0, 0
    snapshots = get_us_stock_snapshots()
    eligible = [
        quote
        for quote in snapshots
        if settings.US_RADAR_MIN_PRICE <= float(quote["price"]) <= settings.US_RADAR_MAX_PRICE
        and float(quote["pct"]) >= settings.US_RADAR_MIN_DAY_CHANGE_PCT
        and float(quote["dollar_volume"]) >= settings.US_RADAR_MIN_DOLLAR_VOLUME
    ]
    eligible.sort(key=lambda quote: (float(quote["pct"]), float(quote["dollar_volume"])), reverse=True)

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
        if _create_candidate(
            store,
            market="US",
            symbol=symbol,
            name=str(quote.get("name") or symbol),
            price=float(quote["price"]),
            pct=float(quote["pct"]),
            volume=float(quote.get("volume") or 0),
            attributes={
                "signal": "低价股放量上涨",
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


def _fetch_candidate_quote(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
    if candidate["market"] == "CN":
        quote = get_stock_quote(candidate["symbol"])
        if not quote:
            return None
        price = _safe_float(quote.get("price"))
        pct = _safe_float(quote.get("pct"))
        if price is None or price <= 0:
            return None
        return {
            "name": str(quote.get("name") or candidate["name"]),
            "price": price,
            "pct": pct,
            "volume": None,
        }
    attributes = candidate.get("attributes") or {}
    if _is_experimental_yahoo_source(attributes):
        try:
            quotes = fetch_yfinance_dev_quotes([str(candidate["symbol"])])
        except Exception as exc:
            log_error(f"❌ Yahoo 实验性追踪报价失败: {exc.__class__.__name__}")
            return None
        if not quotes:
            log_error("❌ Yahoo 实验性追踪报价为空")
            return None
        quote = quotes[0]
        return {
            "name": str(quote.get("name") or candidate["name"]),
            "price": quote.get("price"),
            "pct": quote.get("pct"),
        }
    return get_us_stock_quote(str(candidate["symbol"]))


def _process_active_candidates(store: RadarStore, now: datetime) -> tuple[int, int, int]:
    confirmed = 0
    invalidated = 0
    processed = 0
    for candidate in store.active_candidates(now):
        should_fetch = (
            _is_a_share_trading_session(now)
            if candidate["market"] == "CN"
            else _is_us_trading_session(now)
        )
        if not should_fetch:
            continue
        quote = _fetch_candidate_quote(candidate)
        if not quote:
            log_info(
                f"雷达追踪暂未获得有效报价: {candidate['market']}:{candidate['symbol']}"
            )
            continue
        price = float(quote["price"])
        pct = _safe_float(quote.get("pct"))
        volume = _safe_float(quote.get("volume"))
        store.record_quote(
            market=str(candidate["market"]),
            symbol=str(candidate["symbol"]),
            name=str(quote.get("name") or candidate["name"]),
            price=price,
            pct=pct,
            volume=volume,
            observed_at=now,
        )
        store.update_quote(
            str(candidate["candidate_id"]), price=price, pct=pct, observed_at=now
        )
        candidate["last_price"] = price
        candidate["last_pct"] = pct
        processed += 1

        change_from_initial = (price / float(candidate["initial_price"]) - 1) * 100
        if change_from_initial <= -settings.RADAR_INVALIDATION_PCT:
            if store.close_candidate(
                str(candidate["candidate_id"]), "触及价格失效条件", now
            ):
                _send_tg_with_summary(
                    _format_update_message(candidate, "invalidated"),
                    token=settings.INTERACTION_BOT_TOKEN,
                    chat_id=settings.INTERACTION_CHAT_ID,
                )
                invalidated += 1
            continue

        created_at = datetime.fromisoformat(str(candidate["created_at"]))
        age = now.astimezone(created_at.tzinfo) - created_at
        if (
            candidate["status"] == "auto_tracking"
            and age >= timedelta(minutes=settings.RADAR_CONFIRM_AFTER_MINUTES)
        ):
            store.mark_confirmed(str(candidate["candidate_id"]), now)
            _send_tg_with_summary(
                _format_update_message(candidate, "confirmed"),
                token=settings.INTERACTION_BOT_TOKEN,
                chat_id=settings.INTERACTION_CHAT_ID,
            )
            confirmed += 1
    return processed, confirmed, invalidated


def _close_expired_candidates(store: RadarStore, now: datetime) -> int:
    closed = 0
    for candidate in store.expiring_candidates(now):
        if store.close_candidate(str(candidate["candidate_id"]), "追踪到期", now):
            _send_tg_with_summary(
                _format_update_message(candidate, "expired"),
                token=settings.INTERACTION_BOT_TOKEN,
                chat_id=settings.INTERACTION_CHAT_ID,
            )
            closed += 1
    return closed


def _is_authorized_callback(callback: dict[str, Any]) -> bool:
    return is_authorized_interaction(callback)


def handle_radar_callback(callback: dict[str, Any], now: Optional[datetime] = None) -> str:
    """Apply one button click and return a short Telegram callback notice."""
    now = now or datetime.now(settings.SHA_TZ)
    if not _is_authorized_callback(callback):
        return "此按钮仅允许配置的管理员使用。"
    data = str(callback.get("data") or "")
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != RADAR_CALLBACK_PREFIX:
        return "未知的雷达操作。"
    candidate_id, action = parts[1], parts[2]
    store = RadarStore(settings.MONITOR_DB_FILE)
    store.initialize()
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        return "该候选已不存在。"
    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if candidate.get("telegram_chat_id") and str(chat.get("id")) != str(candidate["telegram_chat_id"]):
        return "该按钮不属于当前追踪消息。"
    if action == "stop":
        if store.close_candidate(candidate_id, "用户停止追踪", now):
            return f"已停止追踪 {candidate['symbol']}。"
        return "该候选已结束，无需重复停止。"
    if action == "close":
        minutes = _market_close_minutes(str(candidate["market"]), now)
        extended = store.extend_candidate(candidate_id, minutes, now)
        return (
            f"已追踪 {candidate['symbol']} 至本市场收盘。"
            if extended
            else "该候选已结束，无法延长。"
        )
    try:
        minutes = int(action)
    except ValueError:
        return "未知的追踪时长。"
    if minutes not in {30, 60, 120, 240}:
        return "不允许的追踪时长。"
    extended = store.extend_candidate(candidate_id, minutes, now)
    return (
        f"已继续追踪 {candidate['symbol']} {minutes} 分钟。"
        if extended
        else "该候选已结束，无法延长。"
    )


def run_radar() -> None:
    """Run one candidate-scan and tracking cycle; no order is ever submitted."""
    now = datetime.now(settings.SHA_TZ)
    if not (
        settings.RADAR_A_SHARE_CODES
        or settings.RADAR_A_SHARE_HOT_POOL_ENABLED
        or settings.POLYGON_API_KEY
        or settings.YFINANCE_EXPERIMENTAL_RADAR_ENABLED
    ):
        raise RuntimeError(
            "雷达未配置任何行情来源：请设置 RADAR_A_SHARE_CODES 或 POLYGON_API_KEY"
        )
    store = RadarStore(settings.MONITOR_DB_FILE)
    store.initialize()
    if not store.acquire_lock("radar", now):
        log_info("实时标的雷达跳过：上一轮尚未结束")
        return
    try:
        a_market_open = _is_a_share_trading_session(now)
        us_market_open = _is_us_trading_session(now)
        a_sampled, a_candidates = _scan_a_share_candidates(store, now)
        a_hot_sampled, a_hot_candidates = _scan_a_share_hot_pool(store, now)
        us_sampled, us_candidates = _scan_us_candidates(store, now)
        if a_market_open and settings.RADAR_A_SHARE_CODES and not a_sampled:
            raise RuntimeError("A 股雷达在交易时段未获取到有效行情")
        if us_market_open and settings.POLYGON_API_KEY and not us_sampled:
            raise RuntimeError("美股雷达在交易时段未获取到有效行情")
        processed, confirmed, invalidated = _process_active_candidates(store, now)
        expired = _close_expired_candidates(store, now)
        _record_fetch_success(True)
        log_info(
            "雷达完成: "
            f"a_sampled={a_sampled}, a_hot_sampled={a_hot_sampled}, us_sampled={us_sampled}, "
            f"new_candidates={a_candidates + a_hot_candidates + us_candidates}, active_processed={processed}, "
            f"confirmed={confirmed}, invalidated={invalidated}, expired={expired}"
        )
    except Exception as exc:
        log_error(f"❌ 实时标的雷达失败: {exc.__class__.__name__}")
        raise
    finally:
        store.release_lock("radar")
