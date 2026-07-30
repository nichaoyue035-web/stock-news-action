import os
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
US_EASTERN_TZ = ZoneInfo("America/New_York")

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
MONITOR_DB_FILE = os.getenv(
    "MONITOR_DB_FILE", os.path.join(BASE_DIR, "monitor.db")
)
RUN_STATUS_FILE = os.getenv(
    "RUN_STATUS_FILE", os.path.join(BASE_DIR, "runtime_status.json")
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

URL_NEWS = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html"
URL_FUNDS = "https://push2.eastmoney.com/api/qt/clist/get"
URL_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"
URL_HISTORY = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
URL_POLYGON_SNAPSHOTS = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
URL_POLYGON_SINGLE_SNAPSHOT = (
    "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
)
URL_POLYGON_NEWS = "https://api.polygon.io/v2/reference/news"

# 海外默认信息源（可通过环境变量覆盖）
# 旧地址 feeds.reuters.com 已经不稳定/失效，改为 Reuters Agency 新 feed 结构
DEFAULT_GLOBAL_RSS = (
    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"
)
GLOBAL_NEWS_RSS = os.getenv("GLOBAL_NEWS_RSS", DEFAULT_GLOBAL_RSS).strip()


def _parse_rss_url_list(raw_value):
    """Parse comma-separated RSS URLs, tolerating Chinese commas and spaces."""
    normalized = str(raw_value or "").replace("，", ",")
    return [url.strip() for url in normalized.split(",") if url.strip()]


def _env_positive_int(name, default):
    """Read a positive integer environment setting with a safe default."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _env_positive_float(name, default):
    """Read a positive float environment setting with a safe default."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _env_enabled(name, default=False):
    """Read an explicit boolean feature switch from the environment."""
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_integer_list(raw_value):
    """Parse a comma-separated allowlist while ignoring malformed entries."""
    values = []
    for item in _parse_rss_url_list(raw_value):
        try:
            value = int(item)
        except ValueError:
            continue
        if value not in values:
            values.append(value)
    return values


# 额外信息源（RSS），支持多个地址，用英文逗号或中文逗号分隔。
# 示例：https://example.com/feed.xml,https://another-site.com/rss
CUSTOM_NEWS_RSS = _parse_rss_url_list(os.getenv("CUSTOM_NEWS_RSS", ""))

# 合并后的外部信息源列表（海外 + 自定义）
EXTERNAL_NEWS_RSS = [url for url in [GLOBAL_NEWS_RSS, *CUSTOM_NEWS_RSS] if url]

# Second-batch dedicated sources. SEC is opt-in because the SEC requires a
# declared user agent and a focused watchlist. The official China sources and
# GDELT are also feature-switched so deployment does not silently broaden news
# coverage before they are verified in the target environment.
SEC_WATCHLIST_TICKERS = [
    ticker.upper()
    for ticker in _parse_rss_url_list(os.getenv("SEC_WATCHLIST_TICKERS", ""))
    if ticker.strip()
]
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()
SEC_MAX_FILINGS_PER_TICKER = _env_positive_int(
    "SEC_MAX_FILINGS_PER_TICKER", 3
)
SEC_EDGAR_ALLOWED_FORMS = tuple(
    form.upper()
    for form in _parse_rss_url_list(
        os.getenv("SEC_EDGAR_ALLOWED_FORMS", "8-K,6-K,10-Q,10-K,20-F,40-F")
    )
)

CSRC_NEWS_ENABLED = _env_enabled("CSRC_NEWS_ENABLED")
SSE_ANNOUNCEMENTS_ENABLED = _env_enabled("SSE_ANNOUNCEMENTS_ENABLED")
CN_OFFICIAL_MAX_ITEMS = _env_positive_int("CN_OFFICIAL_MAX_ITEMS", 10)

GDELT_DISCOVERY_ENABLED = _env_enabled("GDELT_DISCOVERY_ENABLED")
GDELT_DISCOVERY_MAX_RECORDS = _env_positive_int(
    "GDELT_DISCOVERY_MAX_RECORDS", 12
)
GDELT_DISCOVERY_QUERY = os.getenv(
    "GDELT_DISCOVERY_QUERY",
    '"military strike" OR blockade OR "state of emergency" OR "bank run" '
    'OR "sovereign default" OR "nuclear accident" OR tsunami OR '
    '"major earthquake" OR cyberattack OR "oil supply disruption" OR '
    '"payment system outage" OR "trade embargo" OR "capital controls"',
).strip()

# 分钟级监控配置。WATCHLIST_CODES 为空时，监控仍会运行新闻提醒，但跳过行情提醒。
WATCHLIST_CODES = [
    code for code in _parse_rss_url_list(os.getenv("WATCHLIST_CODES", "")) if code
]
MONITOR_NEWS_LOOKBACK_MINUTES = _env_positive_int(
    "MONITOR_NEWS_LOOKBACK_MINUTES", 20
)
MONITOR_NEWS_FRESH_MINUTES = _env_positive_int("MONITOR_NEWS_FRESH_MINUTES", 5)
MONITOR_MARKET_ALERT_DEDUP_MINUTES = _env_positive_int(
    "MONITOR_MARKET_ALERT_DEDUP_MINUTES", 60
)
PRICE_ALERT_MINUTE_CHANGE_PCT = _env_positive_float(
    "PRICE_ALERT_MINUTE_CHANGE_PCT", 1.0
)
PRICE_ALERT_COOLDOWN_MINUTES = _env_positive_int(
    "PRICE_ALERT_COOLDOWN_MINUTES", 15
)
PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES = _env_positive_int(
    "PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES", 3
)

# Interactive market radar. It intentionally uses a separate configured A-share
# list so the existing WATCHLIST_CODES monitor keeps its current behaviour.
RADAR_A_SHARE_CODES = [
    code for code in _parse_rss_url_list(os.getenv("RADAR_A_SHARE_CODES", "")) if code
]
RADAR_A_SHARE_MINUTE_CHANGE_PCT = _env_positive_float(
    "RADAR_A_SHARE_MINUTE_CHANGE_PCT", 1.5
)
RADAR_INITIAL_TRACK_MINUTES = _env_positive_int("RADAR_INITIAL_TRACK_MINUTES", 10)
RADAR_CONFIRM_AFTER_MINUTES = _env_positive_int("RADAR_CONFIRM_AFTER_MINUTES", 2)
RADAR_INVALIDATION_PCT = _env_positive_float("RADAR_INVALIDATION_PCT", 3.0)

# US radar uses Polygon only when a key is explicitly configured. This keeps the
# A-share radar usable without creating a paid external data dependency.
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()

# Explicitly local-development only.  The yfinance probe never joins the radar
# timer, Telegram delivery, or candidate-tracking workflow.
YFINANCE_DEV_TICKERS = _parse_rss_url_list(os.getenv("YFINANCE_DEV_TICKERS", ""))
YFINANCE_DEV_BROAD_SCAN = os.getenv("YFINANCE_DEV_BROAD_SCAN", "").strip() == "1"
YFINANCE_DEV_EVENT_MAX_CANDIDATES = _env_positive_int(
    "YFINANCE_DEV_EVENT_MAX_CANDIDATES", 20
)
YFINANCE_DEV_EVENT_ITEMS_PER_SYMBOL = _env_positive_int(
    "YFINANCE_DEV_EVENT_ITEMS_PER_SYMBOL", 3
)
YFINANCE_DEV_EVENT_MAX_AGE_HOURS = _env_positive_int(
    "YFINANCE_DEV_EVENT_MAX_AGE_HOURS", 24
)

US_RADAR_MIN_PRICE = _env_positive_float("US_RADAR_MIN_PRICE", 1.0)
US_RADAR_MAX_PRICE = _env_positive_float("US_RADAR_MAX_PRICE", 5.0)
US_RADAR_MIN_DAY_CHANGE_PCT = _env_positive_float(
    "US_RADAR_MIN_DAY_CHANGE_PCT", 10.0
)
US_RADAR_MIN_DOLLAR_VOLUME = _env_positive_float(
    "US_RADAR_MIN_DOLLAR_VOLUME", 1_000_000.0
)

# Radar messages go to the monitoring bot by default. A private monitoring chat
# is also safely treated as its own allowed user; group chats must set an
# explicit allowlist before callback buttons can change tracking state.
INTERACTION_BOT_TOKEN = (
    os.getenv("TG_INTERACTION_BOT_TOKEN", "").strip()
    or TG_BOT_TOKEN_MONITOR
    or TG_BOT_TOKEN
)
INTERACTION_CHAT_ID = (
    os.getenv("TG_INTERACTION_CHAT_ID", "").strip()
    or TG_CHAT_ID_MONITOR
    or TG_CHAT_ID
)
INTERACTION_ALLOWED_USER_IDS = _parse_integer_list(
    os.getenv("TG_INTERACTION_ALLOWED_USER_IDS", "")
)
MARKET_ALERT_INTERACTION_ENABLED = _env_enabled(
    "MARKET_ALERT_INTERACTION_ENABLED", False
)

DEFAULT_PROMPTS = {
    "daily": "你是A股投资总监。现在是{report_time}，请只基于以下新闻生成《今日盘前内参》：\n{news_txt}\n\n要求：\n1. 先核对时间：重点分析{report_date}盘前/最近24小时消息，不把旧闻当新催化。\n2. 语言精简但不要过度压缩：每个栏目1-2句，说清结论、原因和影响。\n3. 情绪判断必须结合上方具体新闻，不允许空泛说乐观/谨慎。\n4. 输出格式：\n【核心主线】...\n【利好/利空】利好...；利空...\n【情绪判断】结合新闻说明市场情绪强/中性/弱及原因。\n【今日关注】1-2个最值得盯的方向。",
    "monitor": "你是短线交易员。请浏览以下快讯，筛选出具有【即时交易价值】或【重要市场影响】的消息。\n列表：\n{news_list}\n\n要求：\n1. 宁缺毋滥，只选重要的；普通小公司个股公告、订单、业绩、回购、增减持等低重要性消息不要选，除非涉及停复牌、并购重组、退市/立案、控制权变化或明确会带动板块。\n2. 对每一条筛选出的消息，给出一句简短深刻的逻辑分析（利好谁？利空谁？预期多大？）。\n3. 严格按格式输出（每条一行）：ALERT|序号|逻辑分析\n4. 禁止输出 HTML、Markdown 或任何尖括号标签；只输出纯文本。",
    "after_market": "你是A股收盘复盘员。现在是{report_time}（{report_weekday}）。请只基于以下可核对新闻，补充《每日复盘》的结构化推演：\n{news_txt}\n\n要求：\n1. 只讨论{report_date}（{report_weekday}）已发生的事实；周末不写盘面，周五把“明日”改为“下个交易日/下周”。\n2. 新闻事实会由程序单独展示；不要重复罗列标题，不得补造指数、资金、涨跌或公司数据。\n3. 明确区分事实与推演，所有结论使用“可能、若、需验证”等条件性表述；禁止给出仓位、买卖或收益承诺。\n4. 严格输出：\n【收盘结论】一句话概括已知信息对应的市场结构。\n【确认度】哪些部分已有事实支撑，哪些仍只是推演。\n【传导路径】政策、行业或情绪如何可能传导。\n【A股映射】优先观察的板块或风格，以及条件。\n【后续验证】下个交易日需核对的数据、公告、成交或价格信号。",
    "periodic": "你是A股盘中信息过滤助手。请只基于以下可核对新闻，补充盘中简报的结构化推演：\n{news_txt}\n\n要求：\n1. 程序会单独展示新闻事实；不要重复标题，只提炼当前可能的市场主线。\n2. 不得把单条新闻、盘中波动或未证实消息写成趋势；没有足够依据时明确写“暂未确认”。\n3. 不得给出买卖、仓位或收益承诺，使用条件性语言。\n4. 严格输出：\n【盘中主线】最多两条，说明由哪些已知事实支持。\n【确认度】已确认、待确认及不确定点。\n【传导路径】消息如何可能影响预期、资金或产业链。\n【A股映射】优先观察的板块及需要满足的条件。\n【后续验证】午后成交、价格、公告或后续新闻中应核对的信号。",
    "funds": "你是A股资金面分析助手。现在是{report_time}。程序会单独展示资金流入、流出、涨跌和匹配新闻；请只基于以下数据补充结构化推演：\n\n【流入数据】\n{in_str}\n\n【流出数据】\n{out_str}\n\n【匹配新闻】\n{news_txt}\n\n要求：\n1. 资金与价格同向只能视为初步确认；背离、单日流向或没有匹配新闻时必须明确风险。\n2. 不得编造资金来源、政策、订单或市场数据；没有新闻支撑时明确写“暂未发现明确催化”。\n3. 禁止给出仓位、买卖或收益承诺，只说明观察条件。\n4. 严格输出：\n【资金结论】资金是否集中、同向或分歧。\n【确认度】哪些信号已确认，哪些只是单日现象。\n【传导路径】资金、消息和产业链如何可能传导。\n【A股映射】优先观察哪些板块及上下游，附带条件。\n【后续验证】次日成交、资金连续性、价格及公告需要如何验证。",
    "track": "你今天早上推荐了【{name} ({code})】。\n当前行情：现价 {price}，涨跌幅 {pct}%。\n\n作为游资交易员，请评价当前走势：\n1. 是否符合预期？\n2. 操作建议（持仓/补仓/止损/止盈）？\n3. 简短犀利，100字以内。",
    "global": "你是宏观策略交易员。请浏览过去3小时内的快讯，提炼出最重要的【国际宏观事件或重大突发】：\n{news_txt}\n\n要求：\n1. 过滤噪音，只选出1-3件具有全球市场或A股映射影响的大事（如没大事可回复“无重大事件”）。\n2. 对每件事，进行资金面与情绪面分析。\n3. 必须明确指出可能【利好的板块】和【利空的板块】。\n4. 格式：\n🌍 事件名称：...\n- 📝 核心事实：...\n- 🎯 资金推演：...\n- 🏷️ 映射板块：利好XXX / 利空XXX",
}
