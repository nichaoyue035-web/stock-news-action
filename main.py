"""Application entrypoint for stock-news-action."""

from __future__ import annotations

import os
import json
import sys
from datetime import datetime
from typing import Callable, Final, NoReturn, Tuple

# Keep backward-compatible import behavior when executed as a script.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

AnalysisRunner = Callable[[str], None]
SimpleRunner = Callable[[], None]
Logger = Callable[[str], None]

SUPPORTED_ANALYSIS_MODES: Final[set[str]] = {
    "daily",
    "funds",
    "monitor",
    "periodic",
    "us_premarket",
    "us_periodic",
    "after_market",
    "radar",
    "global",
}

REQUIRED_ENV_BY_MODE: Final[dict[str, tuple[str, ...]]] = {
    "daily": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "funds": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "periodic": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "after_market": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "recommend": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "track": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "review": ("TG_BOT_TOKEN", "TG_CHAT_ID"),
    "monitor": ("TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
    "global": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
    "us_premarket": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "us_periodic": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "daily_health": ("TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
    "radar": ("TG_BOT_TOKEN", "TG_CHAT_ID"),
    "status_panel": ("TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
}


def _fatal(message: str) -> NoReturn:
    """Print fatal error and exit with code 1."""
    print(message)
    raise SystemExit(1)


def _validate_required_env(mode: str) -> None:
    """Fail fast when required environment variables are missing for a run mode."""
    if mode == "telegram_listener":
        configured_bot_pairs = (
            (os.getenv("TG_BOT_TOKEN"), os.getenv("TG_CHAT_ID")),
            (os.getenv("TG_BOT_TOKEN_MONITOR"), os.getenv("TG_CHAT_ID_MONITOR")),
        )
        if any(token and chat_id for token, chat_id in configured_bot_pairs):
            return
        _fatal("❌ Telegram 交互监听缺少已完整配置的机器人和聊天 ID")
    required_names = REQUIRED_ENV_BY_MODE.get(mode)
    if not required_names:
        return

    missing = [name for name in required_names if not os.getenv(name)]
    if missing:
        _fatal(f"❌ 缺少必要环境变量/Secrets ({mode}): {', '.join(missing)}")


def _bootstrap_modules() -> (
    Tuple[SimpleRunner, SimpleRunner, AnalysisRunner, SimpleRunner, Logger, Logger]
):
    """Lazily import runtime modules and return callable handlers."""
    try:
        from core.analyzer import run_recommend, run_track, run_analysis, run_review
        from utils.notifier import log_info, log_error
    except Exception as exc:
        _fatal(f"❌ 模块加载失败: {exc}")

    return run_recommend, run_track, run_analysis, run_review, log_info, log_error


def _resolve_mode(argv: list[str]) -> str:
    """Resolve run mode from command-line args."""
    return argv[1] if len(argv) > 1 else "daily"


class HealthStatusError(RuntimeError):
    """Raised when one persisted mode heartbeat cannot be read."""


def _read_health_status(
    mode: str | None = None,
) -> tuple[dict[str, object], datetime, float]:
    """Read one mode heartbeat, or the legacy latest heartbeat when omitted."""
    from config import settings
    from core.runtime import get_run_status_file

    status_file = get_run_status_file(mode) if mode else settings.RUN_STATUS_FILE

    try:
        with open(status_file, "r", encoding="utf-8") as file:
            status = json.load(file)
        if not isinstance(status, dict):
            raise ValueError("运行状态不是对象")
        finished_at = datetime.fromisoformat(str(status["finished_at"]))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        target = mode or "最近一次任务"
        raise HealthStatusError(f"无法读取 {target} 运行状态: {exc.__class__.__name__}")

    age_seconds = (datetime.now(settings.SHA_TZ) - finished_at).total_seconds()
    return status, finished_at, age_seconds


def _is_healthy_status(status: dict[str, object], age_seconds: float) -> bool:
    """Return whether the latest heartbeat is recent and fully successful."""
    max_age_minutes = int(os.getenv("HEALTH_MAX_AGE_MINUTES", "30"))
    return (
        status.get("status") == "success"
        and age_seconds <= max_age_minutes * 60
    )


def _print_health_status(mode: str | None = None) -> None:
    """Print one VPS heartbeat and fail when it is missing, stale, or failed."""
    try:
        status, _, age_seconds = _read_health_status(mode)
    except HealthStatusError as exc:
        _fatal(f"❌ {exc}")
    max_age_minutes = int(os.getenv("HEALTH_MAX_AGE_MINUTES", "30"))
    print(json.dumps(status, ensure_ascii=False, indent=2))
    run_status = status.get("status")
    if run_status == "failed":
        _fatal("❌ 最近一次任务执行失败")
    if run_status == "partial":
        reason = str(status.get("reason") or "存在未完成的数据源或处理步骤")
        _fatal(f"⚠️ 最近一次任务仅部分完成: {reason}")
    if run_status != "success":
        _fatal(f"❌ 最近一次任务状态异常: {run_status or '未知'}")
    if age_seconds > max_age_minutes * 60:
        _fatal(f"❌ 运行状态已过期: {age_seconds / 60:.0f} 分钟")


def _send_daily_health_reminder() -> None:
    """Send a clear daily Telegram heartbeat for required independent modes."""
    from config import settings
    from utils.notifier import send_tg

    checked_modes = settings.HEALTH_REQUIRED_MODES or ("daily", "monitor")
    records: list[tuple[str, dict[str, object] | None, datetime | None, float | None, str]] = []
    for mode in checked_modes:
        try:
            status, finished_at, age_seconds = _read_health_status(mode)
            records.append((mode, status, finished_at, age_seconds, ""))
        except HealthStatusError as exc:
            records.append((mode, None, None, None, str(exc)))

    healthy = all(
        status is not None
        and age_seconds is not None
        and _is_healthy_status(status, age_seconds)
        for _, status, _, age_seconds, _ in records
    )
    icon = "🟢" if healthy else "🔴"
    health_text = "系统正常" if healthy else "需要检查"
    lines = [f"{icon} {health_text}"]
    for mode, status, finished_at, age_seconds, error in records:
        if status is None or finished_at is None or age_seconds is None:
            lines.append(f"{mode}：无法确认（{error}）")
            continue
        last_telegram = (
            "已成功发送"
            if status.get("telegram_sent")
            else "本轮未触发（正常）"
            if not status.get("telegram_attempted")
            else "发送失败"
        )
        lines.extend(
            (
                f"{mode}：{status.get('status') or '未知'} · {max(0, age_seconds) / 60:.0f} 分钟前",
                f"  数据：{'成功' if status.get('data_fetch_success') else '未确认'} · "
                f"新闻 {status.get('news_count') if status.get('news_count') is not None else '未知'} 条 · "
                f"RSS {status.get('rss_count') if status.get('rss_count') is not None else '未知'} 条 · "
                f"推送：{last_telegram}",
            )
        )
        if status.get("reason"):
            lines.append(f"  原因：{status['reason']}")
    message = "\n".join(lines)
    if not send_tg(
        message,
        token=settings.TG_BOT_TOKEN_MONITOR,
        chat_id=settings.TG_CHAT_ID_MONITOR,
    ):
        _fatal("❌ 每日健康提醒 Telegram 推送失败")
    print(message)
    if not healthy:
        _fatal("❌ 每日健康提醒检测到任务失败、异常或状态过期")


def main() -> None:
    """Program dispatcher for all runtime modes."""
    mode = _resolve_mode(sys.argv)
    if mode == "health":
        _print_health_status(sys.argv[2] if len(sys.argv) > 2 else None)
        return
    if mode == "metrics":
        from core.metrics import format_metrics

        print(format_metrics(sys.argv[2] if len(sys.argv) > 2 else None))
        return
    _validate_required_env(mode)
    if mode == "daily_health":
        _send_daily_health_reminder()
        return
    if mode == "telegram_listener":
        from core.telegram_interaction import run_telegram_listener

        run_telegram_listener()
        return
    if mode == "status_panel":
        from core.telegram_interaction import send_status_panel

        if not send_status_panel():
            _fatal("❌ 监控状态面板发送失败")
        return
    if mode == "maintenance":
        from core.maintenance import run_maintenance

        run_maintenance()
        return
    if mode == "failure":
        from core.failure_notifier import send_failure_alert

        failed_unit = sys.argv[2] if len(sys.argv) > 2 else "未知 systemd 单元"
        send_failure_alert(failed_unit)
        return
    if mode == "yfinance_dev":
        from core.yfinance_dev import run_yfinance_dev_probe

        run_yfinance_dev_probe()
        return
    run_recommend, run_track, run_analysis, run_review, log_info, log_error = (
        _bootstrap_modules()
    )
    log_info(f"🚀 指挥中心启动 | 目标模式: [{mode}]")

    try:
        if mode == "recommend":
            run_recommend()
        elif mode == "track":
            run_track()
        elif mode == "review":
            run_review()
        elif mode in SUPPORTED_ANALYSIS_MODES:
            run_analysis(mode)
        else:
            log_error(f"❌ 未知模式: {mode}")
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as exc:
        log_error(f"❌ 程序执行发生严重错误: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
