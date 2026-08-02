"""Daily A-share medium-term observation selection."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Optional

from config import settings
from core.data_fetcher import (
    get_hot_stocks_data,
    get_news,
    get_stock_history_bars,
    reset_data_source_health,
)
from core.market_calendar import is_cn_a_share_trading_day
from utils.notifier import log_error, log_info


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _relevant_news(name: str, news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep recent source-attributed items that explicitly name the company."""
    clean_name = "".join(str(name or "").split())
    if not clean_name:
        return []
    matches: list[dict[str, Any]] = []
    for item in news:
        if not isinstance(item, dict):
            continue
        text = " ".join(
            str(item.get(key) or "") for key in ("title", "digest", "content")
        )
        if clean_name in "".join(text.split()) and str(item.get("title") or "").strip():
            matches.append(item)
    return matches[:2]


def _build_candidate(
    stock: dict[str, Any], bars: list[dict[str, Any]], news: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Return a conservative medium-term candidate, or ``None`` when incomplete."""
    if len(bars) < 60:
        return None
    closes = [_as_float(item.get("close")) for item in bars]
    volumes = [_as_float(item.get("volume")) for item in bars]
    if any(value is None or value <= 0 for value in closes):
        return None
    if any(value is None or value < 0 for value in volumes):
        return None
    close_values = [float(value) for value in closes]
    volume_values = [float(value) for value in volumes]
    current = close_values[-1]
    ma20 = _mean(close_values[-20:])
    ma60 = _mean(close_values[-60:])
    return_20 = (current / close_values[-21] - 1) * 100
    return_60 = (current / close_values[-60] - 1) * 100
    return_5 = (current / close_values[-6] - 1) * 100
    high_60 = max(close_values[-60:])
    distance_to_high = (high_60 / current - 1) * 100
    volume_ratio = _mean(volume_values[-5:]) / _mean(volume_values[-25:-5])

    if not (
        current > ma20 > ma60
        and return_20 >= settings.SWING_MIN_20D_RETURN_PCT
        and return_60 >= settings.SWING_MIN_60D_RETURN_PCT
        and return_5 <= settings.SWING_MAX_5D_RETURN_PCT
        and distance_to_high <= settings.SWING_MAX_DISTANCE_60D_HIGH_PCT
        and volume_ratio >= settings.SWING_MIN_VOLUME_RATIO
    ):
        return None

    related_news = _relevant_news(str(stock.get("name") or ""), news)
    if not related_news:
        return None
    score = (
        return_60 * 1.5
        + return_20
        + min(volume_ratio, 3.0) * 3.0
        - distance_to_high
    )
    return {
        "name": str(stock.get("name") or "未知"),
        "code": str(stock.get("code") or "").zfill(6),
        "price": current,
        "score": round(score, 2),
        "return_5": round(return_5, 2),
        "return_20": round(return_20, 2),
        "return_60": round(return_60, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "distance_to_high": round(distance_to_high, 2),
        "volume_ratio": round(volume_ratio, 2),
        "news": related_news,
    }


def _load_active_observation(now: datetime) -> Optional[dict[str, Any]]:
    try:
        with open(settings.PICK_FILE, "r", encoding="utf-8") as file:
            pick = json.load(file)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log_error(f"❌ 中期观察记录读取失败: {exc.__class__.__name__}")
        return None
    if not isinstance(pick, dict) or pick.get("strategy") != "medium_term":
        return None
    try:
        selected_at = datetime.fromisoformat(str(pick["selected_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if selected_at.tzinfo is None:
        selected_at = selected_at.replace(tzinfo=settings.SHA_TZ)
    age = now - selected_at.astimezone(settings.SHA_TZ)
    return pick if age <= timedelta(days=settings.SWING_OBSERVATION_DAYS) else None


def _save_pick(pick: dict[str, Any]) -> bool:
    target = settings.PICK_FILE
    temp_path = f"{target}.{os.getpid()}.tmp"
    try:
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(pick, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, target)
        return True
    except OSError as exc:
        log_error(f"❌ 中期观察记录写入失败: {exc.__class__.__name__}")
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return False


def _format_message(candidate: dict[str, Any], now: datetime) -> str:
    from core.formatter import _format_market_message

    evidence = candidate["news"][0]
    title = str(evidence.get("title") or "未提供标题").strip()
    source = str(evidence.get("source") or "未知来源").strip()
    link = str(evidence.get("link") or "").strip()
    summary = (
        f"{candidate['name']}（{candidate['code']}）收盘 {candidate['price']:.2f}。"
        "这是唯一符合本轮中期条件的观察标的。"
    )
    impact = "\n".join(
        (
            f"趋势：20 日 {candidate['return_20']:+.1f}% · 60 日 {candidate['return_60']:+.1f}%；"
            f"收盘高于 20/60 日均线（{candidate['ma20']:.2f}/{candidate['ma60']:.2f}）。",
            f"节奏：近 5 日 {candidate['return_5']:+.1f}%；距 60 日高点 {candidate['distance_to_high']:.1f}%；"
            f"近 5 日成交量约为此前 20 日的 {candidate['volume_ratio']:.1f} 倍。",
            f"可核对信息：[{source}] {title}",
            f"观察期：约 {settings.SWING_OBSERVATION_DAYS} 天；若趋势跌回 60 日均线下方、"
            "相关信息被证伪或出现重大风险公告，需要重新评估。",
            "仅作中期观察，不是交易指令，也不保证未来表现。",
        )
    )
    return _format_market_message(
        "中期观察标的｜A股（约 1–2 个月）",
        report_time=now.strftime("%Y-%m-%d %H:%M"),
        source="东方财富日线 / 近三日公司相关信息",
        category="company",
        importance="medium",
        summary=summary,
        impact=impact,
        links=link or "未知",
        market_scope="公司",
    )


def run_swing() -> None:
    """Select one evidence-backed A-share observation after the close."""
    from core.history import _append_history
    from core.runtime import (
        _record_fetch_success,
        _record_news_summary,
        _record_quality_counts,
        _send_tg_with_summary,
        _set_run_reason,
    )

    reset_data_source_health()
    now = datetime.now(settings.SHA_TZ)
    if not is_cn_a_share_trading_day(now):
        _record_fetch_success(True)
        log_info("中期观察选股跳过：非 A 股交易日")
        return
    active = _load_active_observation(now)
    if active is not None:
        _record_fetch_success(True)
        _record_quality_counts(active_observation=1, new_candidates=0)
        log_info(
            f"中期观察选股跳过：{active.get('name', '当前标的')} 仍在 "
            f"{settings.SWING_OBSERVATION_DAYS} 天观察期内"
        )
        return

    stocks = get_hot_stocks_data()
    _record_fetch_success(bool(stocks))
    if not stocks:
        _set_run_reason("热门股数据为空，无法进行中期观察筛选")
        return
    news = get_news(
        settings.SWING_NEWS_LOOKBACK_MINUTES,
        semantic_dedup=False,
        translate_external=False,
    )
    _record_news_summary(news)
    candidates: list[dict[str, Any]] = []
    history_available = 0
    for stock in stocks:
        code = str(stock.get("code") or "").strip()
        if not code.isdigit():
            continue
        bars = get_stock_history_bars(
            code, now.strftime("%Y-%m-%d"), settings.SWING_HISTORY_SESSIONS
        )
        if len(bars) >= 60:
            history_available += 1
        candidate = _build_candidate(stock, bars, news)
        if candidate is not None:
            candidates.append(candidate)
    _record_quality_counts(
        hot_stocks=len(stocks),
        history_available=history_available,
        qualifying_candidates=len(candidates),
        new_candidates=0,
    )
    if not candidates:
        log_info("中期观察选股完成：没有同时满足趋势、成交和可核对信息的标的")
        return

    chosen = max(candidates, key=lambda item: float(item["score"]))
    pick = {
        "name": chosen["name"],
        "code": chosen["code"],
        "reason": (
            f"中期趋势：20 日 {chosen['return_20']:+.1f}%，60 日 "
            f"{chosen['return_60']:+.1f}%，并有近三日公司相关信息。"
        ),
        "strategy": "medium_term",
        "selected_at": now.isoformat(),
        "observation_days": settings.SWING_OBSERVATION_DAYS,
        "observation_ends_at": (
            now + timedelta(days=settings.SWING_OBSERVATION_DAYS)
        ).isoformat(),
        "metrics": {
            key: chosen[key]
            for key in (
                "price",
                "return_5",
                "return_20",
                "return_60",
                "ma20",
                "ma60",
                "distance_to_high",
                "volume_ratio",
            )
        },
    }
    if not _send_tg_with_summary(_format_message(chosen, now)):
        _set_run_reason("中期观察 Telegram 推送失败", status="failed")
        return
    if not _save_pick(pick):
        _set_run_reason("中期观察已推送，但观察记录写入失败", status="partial")
        return
    if not _append_history(pick, str(chosen["price"])):
        _set_run_reason("中期观察已推送，但历史记录写入失败", status="partial")
        return
    _record_quality_counts(
        hot_stocks=len(stocks),
        history_available=history_available,
        qualifying_candidates=len(candidates),
        new_candidates=1,
    )
    log_info(f"中期观察选股完成: {chosen['code']} {chosen['name']}")
