import os
from datetime import timezone, timedelta

# === 基础环境配置 ===
# 获取项目根目录路径，防止文件读写找不到路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 定义时区 (UTC+8)
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

# === 敏感信息 (从环境变量获取) ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# === 文件路径 ===
PICK_FILE = os.path.join(BASE_DIR, "stock_pick.json")     # 选股记忆文件
PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")     # 外部提示词文件

# === 网络请求配置 ===
# 浏览器身份池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

# API 地址常量 (集中管理)
URL_NEWS = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html"
URL_FUNDS = "https://push2.eastmoney.com/api/qt/clist/get"
URL_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"

# === 默认 Prompt (兜底策略) ===
# 如果 prompts.json 读取失败，将使用这里的默认值
DEFAULT_PROMPTS = {
    "daily": "你是投资总监。基于新闻生成《今日盘前内参》：\n{news_txt}\n\n1.核心主线\n2.利好/利空\n3.情绪判断",
    "monitor": "你是短线交易员。请浏览以下快讯，筛选出具有【即时交易价值】或【重要市场影响】的消息。\n列表：\n{news_list}\n\n要求：\n1. 宁缺毋滥，只选重要的。\n2. 对每一条筛选出的消息，给出一句简短深刻的逻辑分析（利好谁？利空谁？预期多大？）。\n3. 严格按格式输出（每条一行）：ALERT|序号|逻辑分析",
    "after_market": "你是复盘专家。基于下午新闻写《收盘复盘》：\n{news_txt}\n\n1.今日赚钱效应\n2.尾盘变化\n3.明日推演",
    "periodic": "快速总结盘中简报：\n{news_txt}",
    "funds": "你是一位资深A股分析师。这是今日行业资金数据：\n\n主力抢筹：\n{in_str}\n\n主力抛售：\n{out_str}\n\n请分析核心风口、避险板块并给出明日态度。",
    "track": "你今天早上推荐了【{name} ({code})】。\n当前行情：现价 {price}，涨跌幅 {pct}%。\n\n作为游资交易员，请评价当前走势：\n1. 是否符合预期？\n2. 操作建议（持仓/补仓/止损/止盈）？\n3. 简短犀利，100字以内。"
}

# ... (在 PICK_FILE 下面增加一行)
HISTORY_FILE = os.path.join(BASE_DIR, "history.csv")   # 战绩记录表
