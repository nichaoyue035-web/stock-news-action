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
    "after_market",
    "global",
}

REQUIRED_ENV_BY_MODE: Final[dict[str, tuple[str, ...]]] = {
    "daily": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "funds": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN_FUNDS", "TG_CHAT_ID_FUNDS"),
    "periodic": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "after_market": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "recommend": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "track": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "review": ("TG_BOT_TOKEN", "TG_CHAT_ID"),
    "monitor": ("TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
    "global": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
    "daily_health": ("TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
}


def _fatal(message: str) -> NoReturn:
    """Print fatal error and exit with code 1."""
    print(message)
    raise SystemExit(1)


def _validate_required_env(mode: str) -> None:
    """Fail fast when required environment variables are missing for a run mode."""
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


def _read_health_status() -> tuple[dict[str, object], datetime, float]:
    """Read the latest heartbeat and return its status, finish time, and age."""
    from config import settings

    try:
        with open(settings.RUN_STATUS_FILE, "r", encoding="utf-8") as file:
            status = json.load(file)
        if not isinstance(status, dict):
            raise ValueError("运行状态不是对象")
        finished_at = datetime.fromisoformat(str(status["finished_at"]))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        _fatal(f"❌ 无法读取运行状态: {exc.__class__.__name__}")

    age_seconds = (datetime.now(settings.SHA_TZ) - finished_at).total_seconds()
    return status, finished_at, age_seconds


def _is_healthy_status(status: dict[str, object], age_seconds: float) -> bool:
    """Return whether the latest heartbeat is recent and fully successful."""
    max_age_minutes = int(os.getenv("HEALTH_MAX_AGE_MINUTES", "30"))
    return (
        status.get("status") == "success"
        and age_seconds <= max_age_minutes * 60
    )


def _print_health_status() -> None:
    """Print the latest VPS heartbeat and fail when it is missing/stale/failed."""
    status, _, age_seconds = _read_health_status()
    max_age_minutes = int(os.getenv("HEALTH_MAX_AGE_MINUTES", "30"))
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if status.get("status") == "failed":
        _fatal("❌ 最近一次任务执行失败")
    if age_seconds > max_age_minutes * 60:
        _fatal(f"❌ 运行状态已过期: {age_seconds / 60:.0f} 分钟")


def _send_daily_health_reminder() -> None:
    """Send a clear daily Telegram heartbeat for the VPS monitor channel."""
    from config import settings
    from utils.notifier import send_tg

    status, finished_at, age_seconds = _read_health_status()
    healthy = _is_healthy_status(status, age_seconds)
    last_telegram = (
        "已成功发送"
        if status.get("telegram_sent")
        else "本轮未触发（正常）"
        if not status.get("telegram_attempted")
        else "发送失败"
    )
    icon = "🟢" if healthy else "🔴"
    health_text = "正常" if healthy else "需要检查"
    message = "\n".join(
        (
            f"{icon} VPS 每日健康提醒：{health_text}",
            f"最近任务：{status.get('mode') or '未知'}",
            f"完成时间：{finished_at.isoformat()}",
            f"状态：{status.get('status') or '未知'}",
            f"距今：{max(0, age_seconds) / 60:.0f} 分钟",
            "数据："
            f"抓取={'成功' if status.get('data_fetch_success') else '未确认'}；"
            f"新闻={status.get('news_count') if status.get('news_count') is not None else '未知'}；"
            f"RSS={status.get('rss_count') if status.get('rss_count') is not None else '未知'}",
            f"上轮 Telegram：{last_telegram}",
            f"说明：{status.get('reason') or '无'}",
        )
    )
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
        _print_health_status()
        return
    _validate_required_env(mode)
    if mode == "daily_health":
        _send_daily_health_reminder()
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
