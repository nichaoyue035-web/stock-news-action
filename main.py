"""Application entrypoint for stock-news-action."""

from __future__ import annotations

import os
import sys
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
    "funds": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "periodic": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "after_market": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "recommend": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "track": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN", "TG_CHAT_ID"),
    "review": ("TG_BOT_TOKEN", "TG_CHAT_ID"),
    "monitor": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
    "global": ("DEEPSEEK_API_KEY", "TG_BOT_TOKEN_MONITOR", "TG_CHAT_ID_MONITOR"),
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


def _bootstrap_modules() -> Tuple[SimpleRunner, SimpleRunner, AnalysisRunner, SimpleRunner, Logger, Logger]:
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


def main() -> None:
    """Program dispatcher for all runtime modes."""
    mode = _resolve_mode(sys.argv)
    _validate_required_env(mode)
    run_recommend, run_track, run_analysis, run_review, log_info, log_error = _bootstrap_modules()
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
