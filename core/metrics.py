"""Small, secret-free operational metrics persisted beside runtime heartbeats."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from config import settings
from utils.notifier import log_info


def _empty_metrics() -> dict[str, Any]:
    return {
        "updated_at": "",
        "modes": {},
        "sources": {},
        "feedback": {},
        "recent_runs": [],
    }


def _read_metrics(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_metrics()
    if not isinstance(parsed, dict):
        return _empty_metrics()
    parsed.setdefault("updated_at", "")
    parsed.setdefault("modes", {})
    parsed.setdefault("sources", {})
    parsed.setdefault("feedback", {})
    parsed.setdefault("recent_runs", [])
    if not all(
        isinstance(parsed[key], expected)
        for key, expected in (
            ("modes", dict),
            ("sources", dict),
            ("feedback", dict),
            ("recent_runs", list),
        )
    ):
        return _empty_metrics()
    return parsed


@contextmanager
def _metrics_lock(metrics_path: Path) -> Iterator[None]:
    """Serialize concurrent timer completions without a new runtime dependency."""
    lock_path = metrics_path.with_suffix(metrics_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass


def _increment(counter: dict[str, Any], key: str) -> None:
    counter[key] = int(counter.get(key) or 0) + 1


def _write_metrics(path: Path, metrics: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _quality_counts(value: Any) -> dict[str, int]:
    """Return bounded integer pipeline counts suitable for a status file."""
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for raw_name, raw_count in value.items():
        name = str(raw_name).strip().lower()[:48]
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if name and count >= 0:
            counts[name] = count
    return counts


def record_run_metrics(
    summary: dict[str, Any], source_health: dict[str, dict[str, Any]]
) -> None:
    """Aggregate one completed run while keeping the task result independent."""
    mode = str(summary.get("mode") or "unknown")
    status = str(summary.get("status") or "unknown")
    finished_at = str(summary.get("finished_at") or "")
    quality = _quality_counts(summary.get("quality"))
    metrics_path = Path(settings.METRICS_FILE)
    run_record = {
        key: summary.get(key)
        for key in (
            "mode",
            "status",
            "finished_at",
            "duration_seconds",
            "data_fetch_success",
            "news_count",
            "rss_count",
            "ai_called",
            "telegram_attempted",
            "telegram_sent",
            "quality",
        )
    }
    try:
        with _metrics_lock(metrics_path):
            metrics = _read_metrics(metrics_path)
            mode_metrics = metrics["modes"].setdefault(mode, {"runs": 0})
            _increment(mode_metrics, "runs")
            _increment(mode_metrics, status)
            if summary.get("data_fetch_success") is False:
                _increment(mode_metrics, "data_fetch_failed")
            if summary.get("telegram_attempted"):
                _increment(mode_metrics, "telegram_attempted")
                if not summary.get("telegram_sent"):
                    _increment(mode_metrics, "telegram_failed")
            mode_metrics["last_status"] = status
            mode_metrics["last_finished_at"] = finished_at
            mode_metrics["last_duration_seconds"] = summary.get("duration_seconds")
            mode_metrics["last_quality"] = quality
            quality_totals = mode_metrics.setdefault("quality_totals", {})
            if isinstance(quality_totals, dict):
                for name, count in quality.items():
                    quality_totals[name] = int(quality_totals.get(name) or 0) + count

            for name, state in source_health.items():
                if not isinstance(state, dict):
                    continue
                source_metrics = metrics["sources"].setdefault(name, {"checks": 0})
                _increment(source_metrics, "checks")
                source_status = str(state.get("status") or "unknown")
                _increment(source_metrics, source_status)
                source_metrics["last_status"] = source_status
                source_metrics["last_count"] = state.get("count")
                source_metrics["last_finished_at"] = finished_at

            metrics["recent_runs"].append(run_record)
            metrics["recent_runs"] = metrics["recent_runs"][-settings.METRICS_RECENT_RUNS :]
            metrics["updated_at"] = finished_at
            _write_metrics(metrics_path, metrics)
    except OSError as exc:
        log_info(f"运行指标保存失败: {exc.__class__.__name__}")


def record_feedback_metric(channel: str, action: str) -> None:
    """Count an accepted user control without storing identity or message content."""
    clean_channel = str(channel).strip().lower()[:32]
    clean_action = str(action).strip().lower()[:32]
    if not clean_channel or not clean_action:
        return
    metrics_path = Path(settings.METRICS_FILE)
    try:
        with _metrics_lock(metrics_path):
            metrics = _read_metrics(metrics_path)
            channel_metrics = metrics["feedback"].setdefault(clean_channel, {})
            if isinstance(channel_metrics, dict):
                _increment(channel_metrics, clean_action)
            metrics["updated_at"] = datetime.now(settings.SHA_TZ).isoformat()
            _write_metrics(metrics_path, metrics)
    except OSError as exc:
        log_info(f"用户反馈指标保存失败: {exc.__class__.__name__}")


def read_metrics() -> dict[str, Any]:
    """Return persisted metrics, or an empty snapshot before the first run."""
    return _read_metrics(Path(settings.METRICS_FILE))


QUALITY_LABELS = {
    "input_items": "输入",
    "timely_items": "时效通过",
    "eligible_items": "符合规则",
    "new_items": "新增事件",
    "duplicate_alerts_suppressed": "重复拦截",
    "alerts_sent": "提醒送达",
    "quote_samples": "行情样本",
    "new_candidates": "新候选",
    "tracked_candidates": "追踪中",
    "confirmed": "确认延续",
    "invalidated": "失效",
    "expired": "到期",
}


def _format_quality_counts(counts: Any) -> str:
    if not isinstance(counts, dict) or not counts:
        return "暂无"
    return " · ".join(
        f"{QUALITY_LABELS.get(name, name)} {value}"
        for name, value in counts.items()
    )


def format_metrics(mode: str | None = None) -> str:
    """Format a compact operator view suitable for terminal logs or support."""
    metrics = read_metrics()
    selected_mode = str(mode or "").strip().lower()
    modes = metrics["modes"]
    if selected_mode:
        modes = {selected_mode: modes[selected_mode]} if selected_mode in modes else {}
    if not modes:
        return "暂无运行指标。"

    lines = [f"运行指标更新时间：{metrics['updated_at'] or '未知'}"]
    for name in sorted(modes):
        values = modes[name]
        lines.append(
            f"{name}：运行 {values.get('runs', 0)} · 成功 {values.get('success', 0)} · "
            f"部分完成 {values.get('partial', 0)} · 失败 {values.get('failed', 0)} · "
            f"上次 {values.get('last_status', '未知')}"
        )
        lines.append(f"  本轮质量：{_format_quality_counts(values.get('last_quality'))}")
        quality_totals = values.get("quality_totals")
        if isinstance(quality_totals, dict) and quality_totals:
            lines.append(f"  累计质量：{_format_quality_counts(quality_totals)}")
    unhealthy_sources = [
        (name, values)
        for name, values in metrics["sources"].items()
        if values.get("last_status") in {"failed", "partial"}
    ]
    if unhealthy_sources:
        lines.append("最近异常数据源：")
        lines.extend(
            f"- {name}：{values['last_status']}（累计失败 {values.get('failed', 0)} 次）"
            for name, values in sorted(unhealthy_sources)
        )
    feedback = metrics.get("feedback", {})
    if isinstance(feedback, dict) and feedback:
        lines.append("用户反馈：")
        lines.extend(
            f"- {channel}："
            + " · ".join(f"{action} {count}" for action, count in sorted(actions.items()))
            for channel, actions in sorted(feedback.items())
            if isinstance(actions, dict) and actions
        )
    return "\n".join(lines)
