import os
from datetime import timezone, timedelta

# === 1. 账号与密钥配置 ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# === 2. 时间与文件配置 ===
# 定义北京时区 (UTC+8)
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
# 记忆文件名称
PICK_FILE = "stock_pick.json"

# === 3. 网络请求配置 ===
# 浏览器身份池，用于防屏蔽
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

# 东方财富 API 地址
NEWS_API_URL = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html"
MARKET_API_URL = "https://push2.eastmoney.com/api/qt/clist/get"
STOCK_DETAIL_URL = "https://push2.eastmoney.com/api/qt/stock/get"
