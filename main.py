import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def _bootstrap_modules():
    try:
        from core.analyzer import run_recommend, run_track, run_analysis, run_review
        from utils.notifier import log_info, log_error
        return run_recommend, run_track, run_analysis, run_review, log_info, log_error
    except Exception as exc:
        print(f"❌ 模块加载失败: {exc}")
        sys.exit(1)

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    run_recommend, run_track, run_analysis, run_review, log_info, log_error = _bootstrap_modules()
    log_info(f"🚀 指挥中心启动 | 目标模式: [{mode}]")

    try:
        if mode == "recommend":
            run_recommend()
        elif mode == "track":
            run_track()
        elif mode == "review":
            run_review()
        elif mode in ["daily", "funds", "monitor", "periodic", "after_market", "global"]:
            run_analysis(mode)
        else:
            log_error(f"❌ 未知模式: {mode}")
            sys.exit(1)
    except Exception as e:
        log_error(f"❌ 程序执行发生严重错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
