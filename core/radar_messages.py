"""Telegram-safe presentation helpers for the candidate radar."""

from __future__ import annotations

from typing import Any

from config import settings
from core.radar_rules import is_experimental_yahoo_source, market_label


RADAR_CALLBACK_PREFIX = "radar"


def short_text(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def signal_text(attributes: dict[str, Any]) -> str:
    signal = str(attributes.get("signal") or "价格异动")
    if signal == "盘中快速上涨":
        return "价格短时上涨，已自动进入短时追踪。"
    if signal == "盘中快速下跌":
        return "价格短时下跌，已自动进入风险追踪。"
    return "价格、成交与筛选条件已触发，已自动进入短时追踪。"


def candidate_buttons(candidate_id: str) -> dict[str, Any]:
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
                    "text": "停止本次追踪",
                    "callback_data": f"{RADAR_CALLBACK_PREFIX}:{candidate_id}:stop",
                },
                {
                    "text": f"{settings.RADAR_SYMBOL_MUTE_DAYS} 天不再推送",
                    "callback_data": f"{RADAR_CALLBACK_PREFIX}:{candidate_id}:mute",
                },
            ],
        ]
    }


def format_candidate_message(candidate: dict[str, Any]) -> str:
    attributes = candidate["attributes"]
    price = float(candidate["initial_price"])
    pct = candidate.get("initial_pct")
    pct_text = f"{float(pct):+.2f}%" if pct is not None else "未知"
    volume = attributes.get("dollar_volume")
    volume_text = (
        f"${float(volume) / 1_000_000:.1f}M"
        if isinstance(volume, (float, int))
        else "待补充"
    )
    evidence = short_text(attributes.get("evidence") or "行情数据触发")
    catalyst = short_text(attributes.get("catalyst") or "暂未核对到可用的新闻催化。")
    data_limit = (
        "Yahoo 实验性候选池，可能延迟、遗漏或限流；仅作线索，不代表完整市场。"
        if is_experimental_yahoo_source(attributes)
        else ""
    )
    lines = [
        f"🟡 自动追踪｜{market_label(candidate['market'])}",
        f"{candidate['symbol']} {candidate['name']} · {price:.2f} · 当日 {pct_text}",
        f"触发：{evidence}；成交额 {volume_text}",
        f"催化：{catalyst}",
        f"接下来：{signal_text(attributes)}",
        (
            f"停止条件：较触发价回落 {settings.RADAR_INVALIDATION_PCT:.1f}% "
            "以上、行情异常或出现风险信息。"
        ),
    ]
    if data_limit:
        lines.append(f"数据限制：{data_limit}")
    lines.append("仅作观察，不是交易指令。")
    return "\n".join(lines)


def format_update_message(candidate: dict[str, Any], state: str) -> str:
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
        "confirmed": "🟢 追踪继续",
        "invalidated": "🔴 追踪停止",
        "expired": "⚪️ 追踪结束",
    }[state]
    status_text = {
        "confirmed": "初始窗口内未触及停止条件，继续由系统核对。",
        "invalidated": "已触及预设停止条件，不再按这次异动追踪。",
        "expired": "本轮自动追踪到期。",
    }[state]
    return "\n".join(
        (
            title,
            f"{candidate['symbol']} {candidate['name']}｜{market_label(candidate['market'])}",
            f"触发 {initial_price:.2f} · 最新 {last_text} · 相对触发 {change_text}",
            status_text,
            "行情可能延迟或快速反转，请核对最新报价和原始公告。",
        )
    )
