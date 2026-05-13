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
    "daily": "你是A股投资总监。现在是{report_time}，请只基于以下新闻生成《今日盘前内参》：\n{news_txt}\n\n要求：\n1. 先核对时间：重点分析{report_date}盘前/最近24小时消息，不把旧闻当新催化。\n2. 语言精简但不要过度压缩：每个栏目1-2句，说清结论、原因和影响。\n3. 情绪判断必须结合上方具体新闻，不允许空泛说乐观/谨慎。\n4. 输出格式：\n【核心主线】...\n【利好/利空】利好...；利空...\n【情绪判断】结合新闻说明市场情绪强/中性/弱及原因。\n【今日关注】1-2个最值得盯的方向。",
    "monitor": "你是短线交易员。请浏览以下快讯，筛选出具有【即时交易价值】或【重要市场影响】的消息。\n列表：\n{news_list}\n\n要求：\n1. 宁缺毋滥，只选重要的。\n2. 对每一条筛选出的消息，给出一句简短深刻的逻辑分析（利好谁？利空谁？预期多大？）。\n3. 严格按格式输出（每条一行）：ALERT|序号|逻辑分析\n4. 禁止输出 HTML、Markdown 或任何尖括号标签；只输出纯文本。",
    "after_market": "你是A股收盘复盘员。现在是{report_time}，请只基于以下新闻写《收盘复盘》：\n{news_txt}\n\n要求：\n1. 先核对时间：只写{report_date}已发生的事，不把旧闻当今天，不写周末盘面。\n2. 话短但要到位：每点一句话，少形容词，多结论。\n3. 保持精简但信息完整：每个栏目1-2句，说清结论、原因和影响。\n4. 输出格式：\n【结论】一句话说明今天市场强弱。\n【赚钱效应】强/弱在哪里。\n【尾盘变化】资金最后在买什么/卖什么。\n【明日看点】1-2个最关键方向。",
    "periodic": "你是盘中茶歇播报员。请把以下新闻做成简洁易懂的盘中简报：\n{news_txt}\n\n要求：\n1. 先用一句话概括当前市场主线。\n2. 每条新闻都要改写成大白话，短句表达，不照搬标题。\n3. 只保留和市场/板块/个股有关的关键信息，过滤套话。\n4. 每条最多30字，并补一句可能影响：利好/利空谁。\n5. 输出格式：\n【一句话】...\n【重点新闻】\n1. 大白话新闻 —— 影响：...",
    "funds": "你是A股资金面策略员。现在是{report_time}，请结合行业资金、涨跌表现和消息面写《主力资金雷达》：\n\n【主力抢筹】\n{in_str}\n\n【主力抛售】\n{out_str}\n\n【今日消息面】\n{news_txt}\n\n要求：\n1. 语言简洁清晰，但不要只给口号；每点说清资金去向、原因和影响。\n2. 技术面以板块涨跌幅和资金净流入/净流出为依据；消息面必须引用上方新闻。\n3. 明日市场态度要深度思考：结合消息面+技术/资金面，给出仓位、方向和风险。\n4. 输出格式：\n【资金主线】...\n【强弱板块】强：...；弱：...\n【消息验证】哪些新闻在支撑/拖累资金方向。\n【明日态度】仓位建议：...；关注：...；回避：...",
    "track": "你今天早上推荐了【{name} ({code})】。\n当前行情：现价 {price}，涨跌幅 {pct}%。\n\n作为游资交易员，请评价当前走势：\n1. 是否符合预期？\n2. 操作建议（持仓/补仓/止损/止盈）？\n3. 简短犀利，100字以内。",
    "global": "你是宏观策略交易员。请浏览过去3小时内的快讯，提炼出最重要的【国际宏观事件或重大突发】：\n{news_txt}\n\n要求：\n1. 过滤噪音，只选出1-3件具有全球市场或A股映射影响的大事（如没大事可回复“无重大事件”）。\n2. 对每件事，进行资金面与情绪面分析。\n3. 必须明确指出可能【利好的板块】和【利空的板块】。\n4. 格式：\n🌍 事件名称：...\n- 📝 核心事实：...\n- 🎯 资金推演：...\n- 🏷️ 映射板块：利好XXX / 利空XXX"
}
