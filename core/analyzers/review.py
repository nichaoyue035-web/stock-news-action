"""Review-mode analyzer implementation."""

from __future__ import annotations

import csv
import os
from datetime import datetime

from config import settings
from core.data_fetcher import get_stock_quote, reset_data_source_health
from utils.notifier import log_error


def run_review() -> None:
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

        recent_rows = rows[-10:] if len(rows) > 10 else rows
        details: list[str] = []
        skipped_reasons: list[str] = []
        total_rows = len(recent_rows)
        total_count = 0
        win_count = 0
        total_profit = 0.0

        for row in recent_rows:
            curr_quote = get_stock_quote(row["Code"])
            if not curr_quote:
                skipped_reasons.append(f"{row.get('Name', '未知')}：行情为空")
                continue
            try:
                start = float(row["Start_Price"])
                curr = float(curr_quote["price"])
                pct = (curr - start) / start * 100
            except (ValueError, TypeError, ZeroDivisionError):
                skipped_reasons.append(f"{row.get('Name', '未知')}：价格无法计算")
                continue

            total_count += 1
            total_profit += pct
            if pct > 0:
                win_count += 1
            icon = "🔴" if pct > 0 else "🟢"
            details.append(f"{icon} {row['Name']}: {pct:+.2f}%")

        _record_fetch_success(total_count > 0)
        if total_count == 0:
            _send_health_status("历史观察记录没有可用行情数据")
            return
        win_rate = (win_count / total_count) * 100
        avg_profit = total_profit / total_count
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
        summary = (
            f"最近记录: {total_rows} 条，成功计算: {total_count} 条，跳过: {skipped_count} 条\n"
            f"观察样本正收益占比: {win_rate:.0f}%\n"
            f"观察样本平均变化: {avg_profit:+.2f}%\n"
            + "\n".join(details)
            + skipped_text
        )
        _send_tg_with_summary(
            _format_market_message(
                "观察记录复盘辅助",
                report_time=now.strftime("%Y-%m-%d %H:%M"),
                source="history.csv / 东方财富行情",
                category="复盘辅助",
                importance="低（复盘辅助）",
                summary=summary,
                impact="仅用于回看观察记录表现，不能证明策略有效，也不构成后续操作建议。",
                links="未知",
            )
        )
    except Exception as exc:
        log_error(f"复盘失败: {exc}")
        _send_health_status("观察记录复盘发生异常")
