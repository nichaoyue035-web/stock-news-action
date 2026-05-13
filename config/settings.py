import os
from datetime import timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

# 1. 主机器人
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 2. ⚡️ 监控机器人 (副频道)
TG_BOT_TOKEN_MONITOR = os.getenv("TG_BOT_TOKEN_MONITOR")
TG_CHAT_ID_MONITOR = os.getenv("TG_CHAT_ID_MONITOR")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

PICK_FILE = os.path.join(BASE_DIR, "stock_pick.json")
PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.csv")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

URL_NEWS = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html"
URL_FUNDS = "https://push2.eastmoney.com/api/qt/clist/get"
URL_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"

# 海外默认信息源（可通过环境变量覆盖）
# 旧地址 feeds.reuters.com 已经不稳定/失效，改为 Reuters Agency 新 feed 结构
DEFAULT_GLOBAL_RSS = "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"
GLOBAL_NEWS_RSS = os.getenv("GLOBAL_NEWS_RSS", DEFAULT_GLOBAL_RSS)

# 额外信息源（RSS），支持多个地址，用英文逗号分隔。
# 示例：https://example.com/feed.xml,https://another-site.com/rss
CUSTOM_NEWS_RSS = [url.strip() for url in os.getenv("CUSTOM_NEWS_RSS", "").split(",") if url.strip()]

# 合并后的外部信息源列表（海外 + 自定义）
EXTERNAL_NEWS_RSS = [url for url in [GLOBAL_NEWS_RSS, *CUSTOM_NEWS_RSS] if url]

DEFAULT_PROMPTS = {
    "daily": "你是投资总监。基于新闻生成《今日盘前内参》：\n{news_txt}\n\n1.核心主线\n2.利好/利空\n3.情绪判断",
    "monitor": "你是短线交易员。请浏览以下快讯，筛选出具有【即时交易价值】或【重要市场影响】的消息。\n列表：\n{news_list}\n\n要求：\n1. 宁缺毋滥，只选重要的。\n2. 对每一条筛选出的消息，给出一句简短深刻的逻辑分析（利好谁？利空谁？预期多大？）。\n3. 严格按格式输出（每条一行）：ALERT|序号|逻辑分析\n4. 禁止输出 HTML、Markdown 或任何尖括号标签；只输出纯文本。",
    "after_market": "你是复盘专家。基于下午新闻写《收盘复盘》：\n{news_txt}\n\n1.今日赚钱效应\n2.尾盘变化\n3.明日推演",
    "periodic": "你是盘中茶歇播报员。请把以下新闻做成简洁易懂的盘中简报：\n{news_txt}\n\n要求：\n1. 先用一句话概括当前市场主线。\n2. 每条新闻都要改写成大白话，短句表达，不照搬标题。\n3. 只保留和市场/板块/个股有关的关键信息，过滤套话。\n4. 每条最多30字，并补一句可能影响：利好/利空谁。\n5. 输出格式：\n【一句话】...\n【重点新闻】\n1. 大白话新闻 —— 影响：...",
    "funds": "你是一位资深A股分析师。这是今日行业资金数据：\n\n主力抢筹：\n{in_str}\n\n主力抛售：\n{out_str}\n\n请分析核心风口、避险板块并给出明日态度。",
    "track": "你今天早上推荐了【{name} ({code})】。\n当前行情：现价 {price}，涨跌幅 {pct}%。\n\n作为游资交易员，请评价当前走势：\n1. 是否符合预期？\n2. 操作建议（持仓/补仓/止损/止盈）？\n3. 简短犀利，100字以内。",
    "global": "你是宏观策略交易员。请浏览过去3小时内的快讯，提炼出最重要的【国际宏观事件或重大突发】：\n{news_txt}\n\n要求：\n1. 过滤噪音，只选出1-3件具有全球市场或A股映射影响的大事（如没大事可回复“无重大事件”）。\n2. 对每件事，进行资金面与情绪面分析。\n3. 必须明确指出可能【利好的板块】和【利空的板块】。\n4. 格式：\n🌍 事件名称：...\n- 📝 核心事实：...\n- 🎯 资金推演：...\n- 🏷️ 映射板块：利好XXX / 利空XXX"
}
