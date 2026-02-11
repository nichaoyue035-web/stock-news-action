import requests
import logging
from config import settings

# === 配置日志格式 (Pro模式标配) ===
# 这样打印出来的日志会带时间戳，方便排查问题
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("StockBot")

def send_tg(content):
    """
    发送 Telegram 消息的核心函数
    """
    # 检查配置是否存在，不存在则仅打印日志
    if not settings.TG_BOT_TOKEN or not settings.TG_CHAT_ID:
        logger.warning("⚠️ 未配置 Telegram Token 或 Chat ID，跳过消息发送")
        return

    url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TG_CHAT_ID,
        "text": content,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        # 设置超时时间，防止网络卡死
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status() # 如果状态码不是200，抛出异常
    except Exception as e:
        logger.error(f"❌ Telegram 发送失败: {e}")

def log_info(msg):
    """统一的信息打印入口"""
    logger.info(msg)

def log_error(msg):
    """统一的错误打印入口"""
    logger.error(msg)
