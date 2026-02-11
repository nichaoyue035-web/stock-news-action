import sys
from engine.ai_logic import run_recommend, run_track, run_general_analysis

def main():
    # 检查命令行是否传了参数（模式），默认是 daily
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    
    print(f"🚀 指挥中心启动 | 目标模式: {mode}")

    # 1. 如果是选股模式
    if mode == "recommend":
        run_recommend()
        
    # 2. 如果是追踪模式
    elif mode == "track":
        run_track()
        
    # 3. 其他所有分析模式 (daily, monitor, funds, periodic, after_market)
    else:
        run_general_analysis(mode)

if __name__ == "__main__":
    main()
