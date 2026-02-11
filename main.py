import sys
import os

# 将当前目录添加到 sys.path，确保在任何环境下都能找到 core, config 等模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap_modules():
    """延迟加载业务模块，并在依赖缺失时给出明确提示。"""
    try:
        # 注意：这里增加导入了 run_review
        from core.analyzer import run_recommend, run_track, run_analysis, run_review
        from utils.notifier import log_info, log_error
        return run_recommend, run_track, run_analysis, run_review, log_info, log_error
    except ModuleNotFoundError as exc:
        # 常见场景：本地环境没有安装 requests/openai
        print(f"❌ 依赖缺失: {exc.name}")
        print("请先安装依赖后再运行，例如：")
        print("  pip install -r requirements.txt")
        sys.exit(1)
    except ImportError as exc:
        # 捕获 run_review 可能不存在的情况（如果你还没改 analyzer.py）
        print(f"❌ 模块导入错误: {exc}")
        sys.exit(1)


def main():
    # 1. 获取运行模式，默认为 'daily'
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"

    # 接收 run_review
    run_recommend, run_track, run_analysis, run_review, log_info, log_error = _bootstrap_modules()

    log_info(f"🚀 指挥中心启动 | 目标模式: [{mode}]")

    try:
        # 2. 根据模式分发任务
        if mode == "recommend":
            # AI 选股模式
            run_recommend()

        elif mode == "track":
            # 个股追踪模式
            run_track()
            
        elif mode == "review":
            # ✨ 新增：战绩复盘模式
            run_review()

        elif mode in ["daily", "funds", "monitor", "periodic", "after_market"]:
            # 通用分析模式 (早报、资金、监控、复盘)
            run_analysis(mode)

        else:
            log_error(f"❌ 未知模式: {mode}")
            print("支持的模式: recommend, track, review, daily, funds, monitor, periodic, after_market")

    except Exception as e:
        log_error(f"❌ 程序执行发生严重错误: {e}")
        # 在 GitHub Actions 中，非零退出码会让 Workflow 显示为失败🔴，方便你收到报警
        sys.exit(1)


if __name__ == "__main__":
    main()
