from __future__ import annotations

import json
import os
import re
from datetime import datetime
from functools import wraps
from typing import Any, Callable

from config import settings
from core.data_fetcher import get_data_source_health
from utils.notifier import log_info, send_tg

CURRENT_RUN_SUMMARY: dict[str, Any] | None = None


class RunFailedError(RuntimeError):
    """Raised after a run records a failed or partial production result."""


def get_run_status_file(mode: str) -> str:
    """Return the independent, filesystem-safe heartbeat path for one mode."""
    safe_mode = re.sub(r"[^a-z0-9_-]+", "_", str(mode or "unknown").lower())
    return os.path.join(settings.RUN_STATUS_DIR, f"{safe_mode or 'unknown'}.json")


def _start_run_summary(mode: str) -> None:
    global CURRENT_RUN_SUMMARY
    CURRENT_RUN_SUMMARY = {
        "mode": mode,
        "started_at": datetime.now(settings.SHA_TZ).isoformat(),
        "data_fetch_success": None,
        "news_count": None,
        "rss_count": None,
        "ai_called": False,
        "telegram_attempted": False,
        "telegram_sent": False,
        "quality": {},
        "status": None,
        "reason": "",
    }


def _get_run_summary() -> dict[str, Any] | None:
    return CURRENT_RUN_SUMMARY


def _set_run_summary(**updates: Any) -> None:
    summary = _get_run_summary()
    if summary is not None:
        summary.update(updates)


def _set_run_reason(reason: str, status: str | None = None) -> None:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return

    summary = _get_run_summary()
    if summary is None:
        return

    if not summary.get("reason"):
        summary["reason"] = clean_reason
    if status:
        summary["status"] = status


def _record_news_summary(news: list[dict[str, Any]]) -> None:
    health = get_data_source_health()
    rss_state = health.get("海外 RSS", {})
    rss_count = rss_state.get("count")
    summary = _get_run_summary()
    updates: dict[str, Any] = {
        "news_count": len(news),
        "rss_count": rss_count,
    }
    source_failed = any(
        state.get("status") in {"failed", "partial"}
        for name, state in health.items()
        if name != "DeepSeek"
    )
    if summary is None or summary.get("data_fetch_success") is None:
        # An empty lookback window is normal for monitor/global modes.  Only a
        # source failure means data fetching itself failed.
        updates["data_fetch_success"] = not source_failed
    if source_failed:
        updates["status"] = "partial"
    _set_run_summary(**updates)


def _record_fetch_success(success: bool) -> None:
    _set_run_summary(data_fetch_success=success)


def _record_quality_counts(**counts: int) -> None:
    """Attach small, secret-free pipeline counts to the current run heartbeat."""
    clean_counts: dict[str, int] = {}
    for name, value in counts.items():
        try:
            clean_counts[str(name)] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    if clean_counts:
        _set_run_summary(quality=clean_counts)


def _derive_run_status(summary: dict[str, Any]) -> str:
    if summary.get("status"):
        return str(summary["status"])
    if summary.get("telegram_attempted") and summary.get("telegram_sent"):
        return "success"
    if summary.get("telegram_attempted") and not summary.get("telegram_sent"):
        return "failed"
    if summary.get("data_fetch_success") is False:
        return "failed"
    if summary.get("reason"):
        return "partial"
    return "success"


def _print_run_summary() -> None:
    summary = _get_run_summary()
    if summary is None:
        return

    summary["status"] = _derive_run_status(summary)
    finished_at = datetime.now(settings.SHA_TZ)
    summary["finished_at"] = finished_at.isoformat()
    try:
        started_at = datetime.fromisoformat(str(summary.get("started_at") or ""))
        summary["duration_seconds"] = max(
            0, round((finished_at - started_at).total_seconds(), 3)
        )
    except ValueError:
        summary["duration_seconds"] = None
    print("[RUN SUMMARY]")
    for key in (
        "mode",
        "data_fetch_success",
        "news_count",
        "rss_count",
        "ai_called",
        "telegram_attempted",
        "telegram_sent",
        "quality",
        "status",
        "duration_seconds",
        "reason",
    ):
        value = summary.get(key)
        if key == "reason" and not value:
            continue
        if value is None:
            value = "null"
        elif isinstance(value, bool):
            value = str(value).lower()
        print(f"{key}={value}")
    _persist_run_summary(summary)
    from core.metrics import record_run_metrics

    record_run_metrics(summary, get_data_source_health())


def _persist_run_summary(summary: dict[str, Any]) -> None:
    """Atomically persist legacy and per-mode, secret-free heartbeats."""
    mode_status_file = get_run_status_file(str(summary.get("mode") or "unknown"))
    status_files = tuple(dict.fromkeys((settings.RUN_STATUS_FILE, mode_status_file)))
    for status_file in status_files:
        temp_file = f"{status_file}.{os.getpid()}.tmp"
        try:
            status_dir = os.path.dirname(status_file)
            if status_dir:
                os.makedirs(status_dir, exist_ok=True)
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(summary, file, ensure_ascii=False, indent=2)
            os.replace(temp_file, status_file)
        except OSError as exc:
            log_info(f"运行状态保存失败: {exc.__class__.__name__}")


def _with_run_summary(mode_value: str | Callable[..., str]):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            mode = mode_value(*args, **kwargs) if callable(mode_value) else mode_value
            _start_run_summary(str(mode))
            try:
                result = func(*args, **kwargs)
            except BaseException:
                _set_run_summary(status="failed")
                raise
            finally:
                _print_run_summary()

            summary = _get_run_summary() or {}
            if summary.get("status") != "success":
                reason = str(summary.get("reason") or "任务未完整完成")
                raise RunFailedError(f"❌ {mode} 任务失败或部分完成: {reason}")
            return result

        return wrapper

    return decorator


def _send_tg_with_summary(content: Any, **kwargs: Any) -> bool:
    _set_run_summary(telegram_attempted=True)
    try:
        sent = send_tg(content, **kwargs)
    except Exception as exc:
        _set_run_summary(telegram_sent=False, status="failed")
        _set_run_reason(f"telegram send failed: {exc.__class__.__name__}")
        raise
    if not sent:
        _set_run_summary(telegram_sent=False, status="failed")
        _set_run_reason("telegram send failed")
        return False
    summary = _get_run_summary() or {}
    status = "partial" if summary.get("status") == "partial" else "success"
    _set_run_summary(telegram_sent=True, status=status)
    return True


def _print_monitor_filter_summary(
    *,
    input_items: int,
    after_time_filter: int,
    after_keyword_filter: int,
    after_dedup: int,
    final_alert_items: int,
    decision: str,
    reason: str = "",
) -> None:
    print("[FILTER]", flush=True)
    print("mode=monitor", flush=True)
    print(f"input_items={input_items}", flush=True)
    print(f"after_time_filter={after_time_filter}", flush=True)
    print(f"after_keyword_filter={after_keyword_filter}", flush=True)
    print(f"after_dedup={after_dedup}", flush=True)
    print(f"final_alert_items={final_alert_items}", flush=True)
    print(f"decision={decision}", flush=True)
    if reason:
        print(f"reason={reason}", flush=True)


def _format_health_status_message(reason: str, formatter) -> str:
    health = get_data_source_health()
    if "DeepSeek" not in health:
        health["DeepSeek"] = {"status": "skipped", "detail": "未调用", "count": None}

    lines = ["⚠️ 本次任务未完成", str(reason).strip()]
    lines.append("数据源：")
    lines.extend(formatter(name, state) for name, state in health.items())
    return "\n".join(lines)


def _send_health_status(
    reason: str,
    formatter=None,
    token: str | None = None,
    chat_id: str | None = None,
) -> None:
    if formatter is None:
        from core.formatter import _format_source_health_line

        formatter = _format_source_health_line
    failure_markers = (
        "数据为空",
        "未找到",
        "无法",
        "读取失败",
        "发生异常",
        "失败",
        "正文为空",
    )
    status = (
        "failed" if any(marker in reason for marker in failure_markers) else "partial"
    )
    _set_run_reason(reason, status=status)
    message = _format_health_status_message(reason, formatter)
    log_info(message)
    _set_run_summary(telegram_attempted=True)
    try:
        sent = send_tg(message, token=token, chat_id=chat_id)
    except Exception as exc:
        _set_run_summary(telegram_sent=False, status="failed")
        _set_run_reason(f"health telegram send failed: {exc.__class__.__name__}")
        raise
    if not sent:
        _set_run_summary(telegram_sent=False, status="failed")
        _set_run_reason("health telegram send failed")
        return
    _set_run_summary(telegram_sent=True)
