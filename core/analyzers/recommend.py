"""Recommend-mode analyzer implementation."""

from __future__ import annotations

import json
from datetime import datetime

from config import settings
from core.data_fetcher import (
    get_hot_stocks_data,
    get_news,
    get_stock_quote,
    reset_data_source_health,
)
from utils.notifier import log_error, log_info


def run_recommend() -> None:
    """Run AI-assisted observation candidate recommendation mode."""
    from core.formatter import (
        _format_market_message,
        _format_news_prompt_line,
    )
    from core.runtime import (
        _record_fetch_success,
        _record_news_summary,
        _send_health_status,
        _send_tg_with_summary,
        _set_run_reason,
    )
    from core.history import _append_history
    from core.analyzer import (
        _extract_pick_data,
        _get_ai_response_with_health,
        _validate_pick_in_candidates,
        _has_effective_content,
    )

    reset_data_source_health()
    log_info("启动：AI 选股推荐")
    candidates = get_hot_stocks_data()
    _record_fetch_success(bool(candidates))
    if not candidates:
        _send_health_status("热门股数据为空，无法生成观察记录")
        return

    candidates_str = "\n".join(
        [
            f"- {s['name']} (代码:{s['code']}, 涨幅:{s['pct']}, 成交:{s['amount']})"
            for s in candidates
        ]
    )
    news = get_news(720)
    _record_news_summary(news)
    news_txt = "\n".join(
        [_format_news_prompt_line(n, include_time=False) for n in news[:15]]
    )
    base_prompt = (
        "你是极其理性的量化交易员。请从下方的【候选股票列表】中，挑选唯一一只最符合当前市场热点和新闻面的股票。\n\n"
        f"【候选股票列表】:\n{candidates_str}\n\n【近期新闻】:\n{news_txt}\n\n"
        '要求：\n1. 必须从候选列表中选一只，绝对禁止捏造。\n2. 输出 JSON 格式：{"name": "股票名", "code": "6位代码", "reason": "简短理由"}'
    )

    content = _get_ai_response_with_health(base_prompt, temperature=0.1)
    if not content:
        _send_health_status("DeepSeek 没有生成有效摘要")
        return

    pick_data = _extract_pick_data(content)
    if not pick_data:
        _send_health_status("DeepSeek 返回内容无法解析为观察记录")
        return

    pick_data = _validate_pick_in_candidates(pick_data, candidates)
    if not pick_data:
        _send_health_status("DeepSeek 返回了候选列表外的股票，已拒绝生成观察记录")
        return

    quote = get_stock_quote(pick_data["code"])
    _record_fetch_success(bool(quote))
    if not quote:
        _send_health_status("个股行情为空，无法生成观察记录")
        return

    try:
        with open(settings.PICK_FILE, "w", encoding="utf-8") as file:
            json.dump(pick_data, file, ensure_ascii=False, indent=2)
    except Exception as exc:
        log_error(f"❌ 选股结果写入失败: {exc}")
        _set_run_reason("选股结果写入失败", status="failed")
        return

    history_saved = _append_history(pick_data, quote["price"])
    if not history_saved:
        _set_run_reason("历史记录写入失败", status="partial")
    now = datetime.now(settings.SHA_TZ)
    message = _format_market_message(
        "市场观察记录",
        report_time=now.strftime("%Y-%m-%d %H:%M"),
        source="热门股 / 近期新闻 / DeepSeek",
        category="观察记录",
        importance="低（观察记录）",
        summary=f"{pick_data['name']} ({pick_data['code']}) 被记录为观察标的，当前价 {quote['price']}。",
        impact=(
            f"观察理由：{pick_data['reason']}"
            + (
                "\n注意：观察记录已生成，但 history.csv 写入失败。"
                if not history_saved
                else ""
            )
        ),
        links="未知",
    )
    if _has_effective_content(message):
        _send_tg_with_summary(message)
    else:
        _send_health_status("最终 Telegram 正文为空")
