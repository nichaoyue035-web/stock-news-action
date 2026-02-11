import requests
from config import TG_BOT_TOKEN, TG_CHAT_ID

def send_tg(content):
    """
    封装 Telegram 发送逻辑
    """
    # 如果没有配置 Token 或 ID，直接跳过不发消息
    if not TG_BOT_TOKEN or not TG_CHAT_ID: 
        print("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过发送")
        return
        
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TG_CHAT_ID, 
        "text": content, 
        "parse_mode": "HTML", 
        "disable_web_page_preview": True
    }
    
    try:
        resp = requests.post(url, json=data, timeout=10)
        resp.raise_for_status() # 如果请求失败会抛出异常
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")
