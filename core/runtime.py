from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from core.data_fetcher import get_data_source_health
from utils.notifier import log_info, send_tg

CURRENT_RUN_SUMMARY: dict[str, Any] | None = None


def _start_run_summary(mode: str) -> None:
    global CURRENT_RUN_SUMMARY
    CURRENT_RUN_SUMMARY = {
        "mode": mode,
        "data_fetch_success": None,
        "news_count": None,
        "rss_count": None,
        "ai_called": False,
        "telegram_attempted": False,
        "telegram_sent": False,
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
    if summary is None or summary.get("data_fetch_success") is None:
        updates["data_fetch_success"] = bool(news)
    if news and any(
        state.get("status") in {"failed", "partial"}
        for name, state in health.items()
        if name != "DeepSeek"
    ):
        updates["status"] = "partial"
    _set_run_summary(**updates)


def _record_fetch_success(success: bool) -> None:
    _set_run_summary(data_fetch_success=success)


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
    print("[RUN SUMMARY]")
    for key in (
        "mode",
        "data_fetch_success",
        "news_count",
        "rss_count",
        "ai_called",
        "telegram_attempted",
        "telegram_sent",
        "status",
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


def _with_run_summary(mode_value: str | Callable[..., str]):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            mode = mode_value(*args, **kwargs) if callable(mode_value) else mode_value
            _start_run_summary(str(mode))
            try:
                return func(*args, **kwargs)
            except Exception:
                _set_run_summary(status="failed")
                raise
            finally:
                _print_run_summary()

        return wrapper

    return decorator


def _send_tg_with_summary(content: Any, **kwargs: Any) -> None:
    _set_run_summary(telegram_attempted=True)
    try:
        send_tg(content, **kwargs)
    except Exception as exc:
        _set_run_summary(telegram_sent=False, status="failed")
        _set_run_reason(f"telegram send failed: {exc.__class__.__name__}")
        raise
    summary = _get_run_summary() or {}
    status = "partial" if summary.get("status") == "partial" else "success"
    _set_run_summary(telegram_sent=True, status=status)


def _print_monitor_filter_summary(*, input_items: int, after_time_filter: int, after_keyword_filter: int, after_dedup: int, final_alert_items: int, decision: str, reason: str = "") -> None:
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

    lines = ["数据源状态："]
    lines.extend(formatter(name, state) for name, state in health.items())
    if reason:
        lines.append(f"- 结果：{reason}")
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
    _ = (token, chat_id)
    failure_markers = ("数据为空", "未找到", "无法", "读取失败", "发生异常", "失败", "正文为空")
    status = "failed" if any(marker in reason for marker in failure_markers) else "partial"
    _set_run_reason(reason, status=status)
    log_info(_format_health_status_message(reason, formatter))
