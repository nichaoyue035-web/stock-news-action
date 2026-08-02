"""Review-mode analyzer implementation."""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from config import settings
from core.data_fetcher import get_stock_history_closes, reset_data_source_health
from utils.notifier import log_error, log_info

REVIEW_HORIZONS = (1, 5, 20)
MEDIUM_TERM_REVIEW_HORIZONS = (20, 40)


def _calculate_forward_returns(
    start_price: Any,
    closes: list[dict[str, Any]],
    horizons: tuple[int, ...] = REVIEW_HORIZONS,
) -> dict[int, float]:
    """Calculate comparable returns at fixed post-recommendation sessions."""
    try:
        start = float(start_price)
    except (TypeError, ValueError):
        return {}
    if start <= 0:
        return {}

    returns: dict[int, float] = {}
    for horizon in horizons:
        if len(closes) < horizon:
            continue
        try:
            close = float(closes[horizon - 1]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        returns[horizon] = (close - start) / start * 100
    return returns


def run_review(*, strategy: str | None = None) -> None:
    """Run observation history review mode."""
    from core.formatter import _format_market_message
    from core.runtime import (
        _record_fetch_success,
        _send_health_status,
        _send_tg_with_summary,
    )

    reset_data_source_health()
    if not os.path.exists(settings.HISTORY_FILE):
        _send_health_status("未找到历史观察记录")
        return

    try:
        with open(settings.HISTORY_FILE, "r", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

        if strategy:
            rows = [row for row in rows if row.get("Strategy", "legacy") == strategy]
        recent_rows = rows[-10:] if len(rows) > 10 else rows
        horizons = (
            MEDIUM_TERM_REVIEW_HORIZONS
            if strategy == "medium_term"
            else REVIEW_HORIZONS
        )
        strategy_label = "中期观察" if strategy == "medium_term" else "观察记录"
        if not recent_rows:
            log_info(f"{strategy_label}没有可复盘的历史记录")
            return
        details: list[str] = []
        skipped_reasons: list[str] = []
        total_rows = len(recent_rows)
        metrics: dict[int, list[float]] = {horizon: [] for horizon in horizons}

        for row in recent_rows:
            closes = get_stock_history_closes(
                row.get("Code"), row.get("Date", ""), max(horizons)
            )
            forward_returns = _calculate_forward_returns(
                row.get("Start_Price"), closes, horizons
            )
            if not forward_returns:
                skipped_reasons.append(
                    f"{row.get('Name', '未知')}：固定周期行情不足"
                )
                continue

            row_parts = []
            for horizon in horizons:
                if horizon not in forward_returns:
                    continue
                pct = forward_returns[horizon]
                metrics[horizon].append(pct)
                row_parts.append(f"T+{horizon} {pct:+.2f}%")
            details.append(f"{row['Name']}: " + " / ".join(row_parts))

        calculated_rows = len(details)
        _record_fetch_success(calculated_rows > 0)
        if calculated_rows == 0:
            _send_health_status("历史观察记录没有足够的固定周期行情数据")
            return

        metric_lines = []
        for horizon in horizons:
            values = metrics[horizon]
            if not values:
                metric_lines.append(f"T+{horizon}: 暂无完整样本")
                continue
            win_rate = sum(value > 0 for value in values) / len(values) * 100
            avg_profit = sum(values) / len(values)
            metric_lines.append(
                f"T+{horizon}: 样本 {len(values)}，"
                f"胜率（正收益）{win_rate:.0f}%，平均收益 {avg_profit:+.2f}%"
            )

        now = datetime.now(settings.SHA_TZ)
        skipped_count = len(skipped_reasons)
        skipped_text = ""
        if skipped_count:
            skipped_text = (
                f"\n跳过样本: {skipped_count} 条（"
                + "；".join(skipped_reasons[:3])
                + ("；..." if skipped_count > 3 else "")
                + "）"
            )
        caution_lines = []
        if calculated_rows < 3:
            caution_lines.append("样本较少，统计结果仅作粗略参考。")
        if skipped_count > calculated_rows:
            caution_lines.append("行情缺失较多，本次复盘可信度较低。")
        caution_text = ""
        if caution_lines:
            caution_text = "\n提示: " + " ".join(caution_lines)
        summary = (
            f"最近记录: {total_rows} 条，成功计算: {calculated_rows} 条，"
            f"跳过: {skipped_count} 条\n"
            + "\n".join(metric_lines)
            + "\n"
            + "\n".join(details)
            + skipped_text
            + caution_text
        )
        _send_tg_with_summary(
            _format_market_message(
                f"{strategy_label}复盘辅助",
                report_time=now.strftime("%Y-%m-%d %H:%M"),
                source="history.csv / 东方财富行情",
                category="复盘辅助",
                importance="低（复盘辅助）",
                summary=summary,
                impact="仅用于回看观察记录的胜率与表现，不能证明策略有效，也不构成后续操作建议。",
                links="未知",
            )
        )
    except Exception as exc:
        log_error(f"复盘失败: {exc}")
        _send_health_status("观察记录复盘发生异常")
