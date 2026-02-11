import sys
import os

# 将当前目录添加到 sys.path，确保在任何环境下都能找到 core, config 等模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.analyzer import run_recommend, run_track, run_analysis
from utils.notifier import log_info, log_error

def main():
    # 1. 获取运行模式，默认为 'daily'
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    
    log_info(f"🚀 指挥中心启动 | 目标模式: [{mode}]")

    try:
        # 2. 根据模式分发任务
        if mode == "recommend":
            # AI 选股模式
            run_recommend()
            
        elif mode == "track":
            # 个股追踪模式
            run_track()
            
        elif mode in ["daily", "funds", "monitor", "periodic", "after_market"]:
            # 通用分析模式 (早报、资金、监控、复盘)
            run_analysis(mode)
            
        else:
            log_error(f"❌ 未知模式: {mode}")
            print("支持的模式: recommend, track, daily, funds, monitor, periodic, after_market")
            
    except Exception as e:
        log_error(f"❌ 程序执行发生严重错误: {e}")
        # 在 GitHub Actions 中，非零退出码会让 Workflow 显示为失败🔴，方便你收到报警
        sys.exit(1)

if __name__ == "__main__":
    main()
