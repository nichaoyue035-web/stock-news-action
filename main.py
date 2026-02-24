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


def _fatal(message: str) -> NoReturn:
    """Print fatal error and exit with code 1."""
    print(message)
    raise SystemExit(1)


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
