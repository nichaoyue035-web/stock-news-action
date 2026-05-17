"""Track-mode analyzer implementation."""

from __future__ import annotations

import json
import os
from datetime import datetime

from config import settings
from core.data_fetcher import get_stock_quote, reset_data_source_health
from utils.notifier import log_error


def run_track() -> None:
    """Run tracked stock observation mode."""
    from core.analyzer import (
        _format_market_message,
        _get_ai_response_with_health,
        _has_effective_content,
        _record_fetch_success,
        _safe_pct_value,
        _send_health_status,
        _send_tg_with_summary,
        load_prompts,
    )

    reset_data_source_health()
    if not os.path.exists(settings.PICK_FILE):
        _send_health_status("未找到观察标的记录")
        return

    try:
        with open(settings.PICK_FILE, "r", encoding="utf-8") as file:
            pick_data = json.load(file)
    except Exception as exc:
        log_error(f"❌ 读取选股文件失败: {exc}")
        _send_health_status("观察标的记录读取失败")
        return

    try:
        quote = get_stock_quote(pick_data["code"])
        _record_fetch_success(bool(quote))
        if not quote:
            _send_health_status("个股行情为空，无法跟踪观察标的")
            return

        pct_num, pct_for_prompt = _safe_pct_value(quote.get("pct", "-"))
        prompts = load_prompts()
        track_prompt = prompts.get("track", settings.DEFAULT_PROMPTS["track"]).format(
            name=pick_data["name"],
            code=pick_data["code"],
            price=quote["price"],
            pct=pct_for_prompt,
        )
        analysis = _get_ai_response_with_health(track_prompt)
        if not analysis:
            _send_health_status("DeepSeek 没有生成有效摘要")
            return

        icon = "🔴" if pct_num is not None and pct_num > 0 else "🟢"
        now = datetime.now(settings.SHA_TZ)
        message = _format_market_message(
            "观察标的跟踪",
            report_time=now.strftime("%Y-%m-%d %H:%M"),
            source="stock_pick.json / 东方财富行情 / DeepSeek",
            category="观察记录",
            importance="低（观察记录）",
            summary=f"{icon} {pick_data['name']} ({pick_data['code']}) 当前价 {quote['price']}，涨跌幅 {pct_for_prompt}%。",
            impact=f"观察观点：{analysis}",
            links="未知",
        )
        if _has_effective_content(message):
            _send_tg_with_summary(message)
        else:
            _send_health_status("最终 Telegram 正文为空")
    except Exception as exc:
        log_error(f"❌ 追踪失败: {exc}")
        _send_health_status("观察标的跟踪发生异常")
