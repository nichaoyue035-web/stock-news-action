import requests
import logging
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("StockBot")

def send_tg(content, token=None, chat_id=None):
    use_token = token if token else settings.TG_BOT_TOKEN
    use_chat_id = chat_id if chat_id else settings.TG_CHAT_ID

    if not use_token or not use_chat_id:
        logger.warning("⚠️ 未配置 Telegram Token 或 Chat ID，跳过消息发送")
        return

    url = f"https://api.telegram.org/bot{use_token}/sendMessage"
    payload = {
        "chat_id": use_chat_id,
        "text": content,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"❌ Telegram 发送失败: {e}")

def log_info(msg):
    logger.info(msg)

def log_error(msg):
    logger.error(msg)
