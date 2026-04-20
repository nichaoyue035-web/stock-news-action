import requests
import logging
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("StockBot")

def log_error(message):
    logger.error(message)

def send_tg(content, token=None, chat_id=None):
    use_token = token if token else settings.TG_BOT_TOKEN
    use_chat_id = chat_id if chat_id else settings.TG_CHAT_ID

    if not use_token or not use_chat_id:
        raise ValueError("Missing TG_BOT_TOKEN or TG_CHAT_ID")

    url = f"https://api.telegram.org/bot{use_token}/sendMessage"
    payload = {
        "chat_id": use_chat_id,
        "text": content,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    resp = requests.post(url, json=payload, timeout=10)

    logger.info(f"Telegram status_code: {resp.status_code}")
    logger.info(f"Telegram response_text: {resp.text}")

    resp.raise_for_status()
