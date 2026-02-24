"""
本地高频监控调度器 (边缘节点守护进程)
"""
import os
import time
from datetime import datetime
import pytz
import schedule

# 必须在导入其他模块前加载 .env 文件
from dotenv import load_dotenv
load_dotenv()

# 导入核心业务逻辑
from core.analyzer import run_analysis
from utils.notifier import log_info, log_error

# 设定时区与交易时间规则
SHA_TZ = pytz.timezone("Asia/Shanghai")

def is_trading_time() -> bool:
    """判断当前是否为 A 股交易时间 (含容错冗余)"""
    now = datetime.now(SHA_TZ)
    
    # 排除周末
    if now.weekday() > 4:
        return False
        
    current_time = now.time()
    
    # 早盘 09:25 - 11:35 (提前5分钟监控集合竞价，延后5分钟处理尾流)
    morning_start = datetime.strptime("09:25", "%H:%M").time()
    morning_end = datetime.strptime("11:35", "%H:%M").time()
    
    # 午盘 12:55 - 15:05 (提前5分钟预热，延后5分钟处理尾单)
    afternoon_start = datetime.strptime("12:55", "%H:%M").time()
    afternoon_end = datetime.strptime("15:05", "%H:%M").time()
    
    if (morning_start <= current_time <= morning_end) or (afternoon_start <= current_time <= afternoon_end):
        return True
        
    return False

def monitor_job():
    """高频监控任务包装器 (带异常隔离)"""
    if not is_trading_time():
        return
        
    try:
        log_info("⚡ 触发高频监控轮询 (Monitor)...")
        # 调用你在 Mac 上通过 Codex 重构好的特征过滤版 monitor
        run_analysis("monitor")
    except Exception as e:
        log_error(f"❌ 高频监控任务崩溃，已隔离: {e}")

def setup_scheduler():
    """注册调度任务"""
    # 盘中每 60 秒轮询一次（可根据东方财富接口限流情况微调，例如改为 every(2).minutes）
    schedule.every(1).minutes.do(monitor_job)
    
    log_info("🚀 本地高频调度器已启动，等待进入交易时间...")
    
    while True:
        schedule.run_pending()
        time.sleep(1) # 降低 CPU 空转功耗

if __name__ == "__main__":
    setup_scheduler()
