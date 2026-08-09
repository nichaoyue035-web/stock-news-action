import os
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
US_EASTERN_TZ = ZoneInfo("America/New_York")

# Stateful production files must be able to live outside a replaceable checkout.
# Local development keeps them in an ignored directory instead of modifying the
# repository's tracked sample files.
STATE_DIR = os.getenv("STATE_DIR", "").strip()
_STATE_BASE_DIR = STATE_DIR or os.path.join(BASE_DIR, ".state")

# 1. 主机器人
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 2. ⚡️ 监控机器人 (副频道)
TG_BOT_TOKEN_MONITOR = os.getenv("TG_BOT_TOKEN_MONITOR")
TG_CHAT_ID_MONITOR = os.getenv("TG_CHAT_ID_MONITOR")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

PICK_FILE = os.getenv("PICK_FILE", os.path.join(_STATE_BASE_DIR, "stock_pick.json"))
PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")
HISTORY_FILE = os.getenv("HISTORY_FILE", os.path.join(_STATE_BASE_DIR, "history.csv"))
MONITOR_DB_FILE = os.getenv(
    "MONITOR_DB_FILE", os.path.join(_STATE_BASE_DIR, "monitor.db")
)
RUN_STATUS_FILE = os.getenv(
    "RUN_STATUS_FILE", os.path.join(_STATE_BASE_DIR, "runtime_status.json")
)
RUN_STATUS_DIR = os.getenv(
    "RUN_STATUS_DIR", os.path.join(os.path.dirname(RUN_STATUS_FILE), "runtime_status")
)
METRICS_FILE = os.getenv(
    "METRICS_FILE", os.path.join(os.path.dirname(RUN_STATUS_FILE), "runtime_metrics.json")
)
STATE_BACKUP_DIR = os.getenv(
    "STATE_BACKUP_DIR", os.path.join(os.path.dirname(MONITOR_DB_FILE), "backups")
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


def _parse_mode_positive_ints(raw_value):
    """Parse mode=minutes entries, ignoring malformed or non-positive values."""
    values = {}
    for item in _parse_rss_url_list(raw_value):
        mode, separator, raw_minutes = item.partition("=")
        if not separator:
            continue
        try:
            minutes = int(raw_minutes.strip())
        except ValueError:
            continue
        clean_mode = mode.strip().lower()
        if clean_mode and minutes > 0:
            values[clean_mode] = minutes
    return values


# A timer can only express weekdays.  Keep exceptional exchange closures
# explicit and deployment-owned instead of treating a market holiday as a data
# failure.  Dates use YYYY-MM-DD and may be separated by English or Chinese
# commas.
CN_MARKET_HOLIDAYS = frozenset(
    _parse_rss_url_list(os.getenv("CN_MARKET_HOLIDAYS", ""))
)
US_MARKET_HOLIDAYS = frozenset(
    _parse_rss_url_list(os.getenv("US_MARKET_HOLIDAYS", ""))
)

# The daily heartbeat checks independent mode files, never whichever mode wrote
# the shared compatibility heartbeat most recently.
HEALTH_REQUIRED_MODES = tuple(
    mode.lower()
    for mode in _parse_rss_url_list(os.getenv("HEALTH_REQUIRED_MODES", "daily,monitor"))
)
# The daily report is expected once per trading day, while monitor and listener
# heartbeats need minute-level freshness.  This setting is for the Telegram
# status panel; the daily-health job retains its stricter post-report check.
STATUS_PANEL_MAX_AGE_MINUTES = _parse_mode_positive_ints(
    os.getenv(
        "STATUS_PANEL_MAX_AGE_MINUTES",
        "daily=4320,monitor=15,telegram_listener=3,source_canary=1560",
    )
)

DB_RETENTION_DAYS = _env_positive_int("DB_RETENTION_DAYS", 30)
DB_BACKUP_RETENTION_DAYS = _env_positive_int("DB_BACKUP_RETENTION_DAYS", 14)
HTTP_GET_MAX_ATTEMPTS = _env_positive_int("HTTP_GET_MAX_ATTEMPTS", 2)
HTTP_GET_RETRY_BASE_SECONDS = _env_positive_float("HTTP_GET_RETRY_BASE_SECONDS", 0.5)
METRICS_RECENT_RUNS = _env_positive_int("METRICS_RECENT_RUNS", 100)
# Keep systemd failures visible in logs and health heartbeats without
# interrupting Telegram for every transient or partial run by default.
TELEGRAM_FAILURE_ALERTS_ENABLED = _env_enabled(
    "TELEGRAM_FAILURE_ALERTS_ENABLED", False
)
OFFSITE_BACKUP_ENABLED = _env_enabled("OFFSITE_BACKUP_ENABLED")
OFFSITE_BACKUP_RCLONE_TARGET = os.getenv("OFFSITE_BACKUP_RCLONE_TARGET", "").strip()
OFFSITE_BACKUP_TIMEOUT_SECONDS = _env_positive_int(
    "OFFSITE_BACKUP_TIMEOUT_SECONDS", 120
)


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
TRUMP_MEDIA_RELAY_ENABLED = _env_enabled("TRUMP_MEDIA_RELAY_ENABLED")
TRUMP_MEDIA_RELAY_MAX_RECORDS = _env_positive_int(
    "TRUMP_MEDIA_RELAY_MAX_RECORDS", 10
)
TRUMP_MEDIA_RELAY_QUERY = os.getenv(
    "TRUMP_MEDIA_RELAY_QUERY",
    '"Donald Trump" "Truth Social" when:1d',
).strip()

# Truth Social has no credential configured here: this opt-in adapter reads only
# the public posts returned by the account-status endpoint.  Keep the volume
# bounded because the monitor can invoke news collection frequently.
TRUTH_SOCIAL_ENABLED = _env_enabled("TRUTH_SOCIAL_ENABLED")
TRUTH_SOCIAL_ACCOUNT_ID = os.getenv(
    "TRUTH_SOCIAL_ACCOUNT_ID", "107780257626128497"
).strip()
TRUTH_SOCIAL_ACCOUNT_USERNAME = os.getenv(
    "TRUTH_SOCIAL_ACCOUNT_USERNAME", "realDonaldTrump"
).strip().lstrip("@")
TRUTH_SOCIAL_MAX_POSTS = min(
    40, _env_positive_int("TRUTH_SOCIAL_MAX_POSTS", 10)
)
GDELT_DISCOVERY_QUERY = os.getenv(
    "GDELT_DISCOVERY_QUERY",
    '"military strike" OR blockade OR "state of emergency" OR "bank run" '
    'OR "sovereign default" OR "nuclear accident" OR tsunami OR '
    '"major earthquake" OR cyberattack OR "oil supply disruption" OR '
    '"payment system outage" OR "trade embargo" OR "capital controls"',
).strip()

# 五分钟监控配置。WATCHLIST_CODES 为空时，监控仍会运行新闻提醒，但跳过行情提醒。
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
    "PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES", 6
)

# Medium-term A-share observation selection.  This is deliberately separate
# from the minute-level radar: it runs after the close and only emits one
# evidence-backed observation at a time.
SWING_HISTORY_SESSIONS = _env_positive_int("SWING_HISTORY_SESSIONS", 65)
SWING_MIN_20D_RETURN_PCT = _env_positive_float("SWING_MIN_20D_RETURN_PCT", 3.0)
SWING_MIN_60D_RETURN_PCT = _env_positive_float("SWING_MIN_60D_RETURN_PCT", 8.0)
SWING_MAX_5D_RETURN_PCT = _env_positive_float("SWING_MAX_5D_RETURN_PCT", 12.0)
SWING_MAX_DISTANCE_60D_HIGH_PCT = _env_positive_float(
    "SWING_MAX_DISTANCE_60D_HIGH_PCT", 12.0
)
SWING_MIN_VOLUME_RATIO = _env_positive_float("SWING_MIN_VOLUME_RATIO", 1.0)
SWING_NEWS_LOOKBACK_MINUTES = _env_positive_int(
    "SWING_NEWS_LOOKBACK_MINUTES", 3 * 24 * 60
)
SWING_OBSERVATION_DAYS = _env_positive_int("SWING_OBSERVATION_DAYS", 45)

# Interactive market radar. It intentionally uses a separate configured A-share
# list so the existing WATCHLIST_CODES monitor keeps its current behaviour.
RADAR_A_SHARE_CODES = [
    code for code in _parse_rss_url_list(os.getenv("RADAR_A_SHARE_CODES", "")) if code
]
RADAR_A_SHARE_MINUTE_CHANGE_PCT = _env_positive_float(
    "RADAR_A_SHARE_MINUTE_CHANGE_PCT", 1.5
)
RADAR_A_SHARE_HOT_POOL_ENABLED = _env_enabled(
    "RADAR_A_SHARE_HOT_POOL_ENABLED", False
)
RADAR_A_SHARE_HOT_POOL_MIN_PRICE = _env_positive_float(
    "RADAR_A_SHARE_HOT_POOL_MIN_PRICE", 2.0
)
RADAR_A_SHARE_HOT_POOL_MAX_PRICE = _env_positive_float(
    "RADAR_A_SHARE_HOT_POOL_MAX_PRICE", 30.0
)
RADAR_A_SHARE_HOT_POOL_MIN_DAY_CHANGE_PCT = _env_positive_float(
    "RADAR_A_SHARE_HOT_POOL_MIN_DAY_CHANGE_PCT", 2.0
)
RADAR_A_SHARE_HOT_POOL_MAX_DAY_CHANGE_PCT = _env_positive_float(
    "RADAR_A_SHARE_HOT_POOL_MAX_DAY_CHANGE_PCT", 8.0
)
RADAR_A_SHARE_HOT_POOL_MAX_NEW_CANDIDATES = _env_positive_int(
    "RADAR_A_SHARE_HOT_POOL_MAX_NEW_CANDIDATES", 1
)
RADAR_INITIAL_TRACK_MINUTES = _env_positive_int("RADAR_INITIAL_TRACK_MINUTES", 10)
RADAR_CONFIRM_AFTER_MINUTES = _env_positive_int("RADAR_CONFIRM_AFTER_MINUTES", 3)
RADAR_CONFIRM_MAX_REVERSAL_PCT = _env_positive_float(
    "RADAR_CONFIRM_MAX_REVERSAL_PCT", 0.5
)
RADAR_INVALIDATION_PCT = _env_positive_float("RADAR_INVALIDATION_PCT", 3.0)
RADAR_MAX_CANDIDATES_PER_SYMBOL_PER_SESSION = _env_positive_int(
    "RADAR_MAX_CANDIDATES_PER_SYMBOL_PER_SESSION", 1
)
RADAR_SYMBOL_MUTE_DAYS = _env_positive_int("RADAR_SYMBOL_MUTE_DAYS", 7)

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
YFINANCE_EXPERIMENTAL_RADAR_ENABLED = _env_enabled(
    "YFINANCE_EXPERIMENTAL_RADAR_ENABLED", False
)
YFINANCE_EXPERIMENTAL_RADAR_INTERVAL_MINUTES = _env_positive_int(
    "YFINANCE_EXPERIMENTAL_RADAR_INTERVAL_MINUTES", 10
)
YFINANCE_EXPERIMENTAL_RADAR_MAX_NEW_CANDIDATES = _env_positive_int(
    "YFINANCE_EXPERIMENTAL_RADAR_MAX_NEW_CANDIDATES", 1
)

US_RADAR_MIN_PRICE = _env_positive_float("US_RADAR_MIN_PRICE", 1.0)
US_RADAR_MAX_PRICE = _env_positive_float("US_RADAR_MAX_PRICE", 5.0)
US_RADAR_MIN_DAY_CHANGE_PCT = _env_positive_float(
    "US_RADAR_MIN_DAY_CHANGE_PCT", 3.0
)
US_RADAR_MAX_DAY_CHANGE_PCT = _env_positive_float(
    "US_RADAR_MAX_DAY_CHANGE_PCT", 15.0
)
US_RADAR_MIN_DOLLAR_VOLUME = _env_positive_float(
    "US_RADAR_MIN_DOLLAR_VOLUME", 1_000_000.0
)

# Real-time candidate tracking belongs to the primary bot, the same bot used by
# the daily observation pick and its follow-up. Market-event buttons remain on
# the monitoring bot so their follow-up messages stay in that channel.
RADAR_INTERACTION_BOT_TOKEN = TG_BOT_TOKEN
RADAR_INTERACTION_CHAT_ID = TG_CHAT_ID
MARKET_INTERACTION_BOT_TOKEN = TG_BOT_TOKEN_MONITOR
MARKET_INTERACTION_CHAT_ID = TG_CHAT_ID_MONITOR

# A private interaction chat is safely treated as its own allowed user; group
# chats must set an explicit allowlist before callback buttons can change state.
INTERACTION_ALLOWED_USER_IDS = _parse_integer_list(
    os.getenv("TG_INTERACTION_ALLOWED_USER_IDS", "")
)
MARKET_ALERT_INTERACTION_ENABLED = _env_enabled(
    "MARKET_ALERT_INTERACTION_ENABLED", False
)

DEFAULT_PROMPTS = {
    "daily": "你是A股市场编辑。现在是{report_time}。只根据下列新闻，写一条便于手机阅读的盘前简报：\n{news_txt}\n\n重点是{report_date}盘前和最近24小时；旧闻不能当作新催化。事实会单独展示，不要重复标题，不要编造数据或给买卖建议。可以用你掌握的通用历史模式做类比，但没有可靠案例时写“暂无可靠历史类比”，禁止编造事件名称、样本数、胜率或涨跌幅。预测必须写成立条件，不能把它写成确定涨跌。直接输出短句，不使用【】或报告腔：\n主线：最多两条\n历史类比：过去类似事件通常如何演变；没有把握就写暂无可靠历史类比\n预测：未来1-3个交易日最可能的路径与成立条件\n风险与验证：1-2个会推翻判断的信号\n可信度：0-100%，只表示本次证据完整度、来源可信度和历史可比性的模型判断，不代表涨跌概率。",
    "monitor": "五分钟监控使用确定性规则，只即时发送经济相关的已确认或待核实硬风险；普通重要消息进入三小时市场总结。",
    "monitor_urgent": "你是经济风险快讯编辑。只根据下面这条已经通过规则筛选的经济风险事件，补充极短的历史相关和条件预测。\n事件：{news_txt}\n\n不要编造历史事件名称、样本数量、胜率、涨跌幅或实时价格；没有可靠历史依据时明确写“暂无可靠历史类比”。预测不能写成确定涨跌，必须写成立条件。只输出以下四行，不使用【】和套话：\n历史类比：过去相似的经济风险通常如何传导；没有把握就写暂无可靠历史类比\n预测：未来数小时至1-3个交易日的可能走势与成立条件\n验证点：最关键的确认或推翻信号\n可信度：0-100%，表示事实确认度、来源可信度和历史可比性的模型判断，不代表涨跌概率。",
    "after_market": "你是A股收盘复盘编辑。现在是{report_time}（{report_weekday}）。只根据下列可核对新闻，补充一条简短复盘：\n{news_txt}\n\n只讨论{report_date}已发生的事；事实会单独展示，不要重复标题或编造盘面数据。可用通用历史模式做类比，但没有可靠案例时写暂无可靠历史类比，禁止编造历史样本或涨跌统计。预测要写条件，不给买卖建议。直接输出短句，不使用【】：\n收盘观察：…\n历史类比：…\n预测：下个交易日最可能的路径与成立条件\n验证点：…\n可信度：0-100%，表示证据与历史可比性的模型判断，不代表涨跌概率。",
    "periodic": "你是A股盘中信息过滤助手。只根据下列可核对新闻，补充一条便于手机阅读的盘中简报：\n{news_txt}\n\n事实会单独展示，不重复标题。单条新闻、盘中波动或未证实消息不能写成趋势；没有可靠历史类比就写暂无可靠历史类比，不要编造历史统计。预测要写成立条件，不提供买卖建议。直接输出短句，不使用【】：\n主线：最多两条\n历史类比：…\n预测：午后至下个交易日的可能路径与条件\n验证点：…\n可信度：0-100%，表示本次模型对证据完整度的判断，不代表涨跌概率。",
    "us_premarket": "你是美股盘前信息过滤助手。现在是美东 {report_time}（{report_weekday}）。只根据下列可核对新闻写一条盘前简报：\n{news_txt}\n\n只保留1-3个与美股或全球联动有关的关键事实。没有提供期货、盘前价格、成交额或期权数据时，不能编造盘面，也不能写成确定涨跌。可以用通用历史模式做类比，但没有可靠案例时写暂无可靠历史类比，禁止编造历史统计。不提供买卖建议。直接输出短句，不使用【】：\n隔夜重点：…\n历史类比：…\n预测：开盘后可能路径与成立条件\n验证点：…\n可信度：0-100%，表示证据完整度和历史可比性的模型判断，不代表涨跌概率。",
    "us_periodic": "你是美股盘中信息过滤助手。现在是美东 {report_time}（{report_weekday}）。只根据下列可核对新闻写一条午间简报：\n{news_txt}\n\n事实会单独展示，不重复标题。未提供实时指数、价格、成交或期权数据时，不假设盘面表现，也不把单条新闻写成趋势。没有可靠历史类比就写暂无可靠历史类比，禁止编造统计。不提供买卖建议。直接输出短句，不使用【】：\n焦点：1-2条\n历史类比：…\n预测：下午至下个交易日的可能路径与条件\n验证点：…\n可信度：0-100%，表示证据完整度和历史可比性的模型判断，不代表涨跌概率。",
    "funds": "你是A股资金面分析助手。现在是{report_time}。程序会单独展示资金流入、流出、涨跌和匹配新闻；只根据下列数据补充简明解读：\n\n流入\n{in_str}\n\n流出\n{out_str}\n\n匹配新闻\n{news_txt}\n\n资金与价格同向只算初步信号；背离、单日流向或没有新闻支撑时必须说清风险。可用通用历史模式做类比，但没有可靠案例时写暂无可靠历史类比，禁止编造资金来源、历史胜率或市场数据。预测要写条件，不提供买卖建议。直接输出短句，不使用【】：\n结论：…\n历史类比：…\n预测：未来1-3个交易日资金与价格可能如何演变及条件\n验证点：成交、资金连续性、价格或公告\n可信度：0-100%，表示证据完整度和历史可比性的模型判断，不代表涨跌概率。",
    "track": "观察标的：{name}（{code}）。当前价 {price}，涨跌幅 {pct}%。\n\n只根据这些信息，用不超过3句说明：变化、仍需确认的条件、风险。不得提供持仓、补仓、止损、止盈或其他买卖建议。语言直接，不使用【】。",
    "global": "你是市场信息编辑。根据过去3小时内已筛选的重要新闻，写一条简洁市场更新。\n新闻：\n{news_txt}\n\n只保留1-3个真正改变市场定价、政策预期、流动性、行业供需或风险偏好的事件；重复报道合并。若没有实质变化，只回复“无重要市场变化”。每个事件用4行：\n- 发生：已确认的事实\n  历史类比：过去类似事件常见的传导或演变；没有可靠案例写暂无可靠历史类比，禁止编造历史统计\n  预测：未来数小时至1-3个交易日的可能路径与成立条件\n  接着看：能验证或推翻判断的公告、数据或价格信号\n末尾单独写：可信度：0-100%，只表示证据完整度、来源可信度和历史可比性的模型判断，不代表涨跌概率。避免套话、确定性涨跌判断和买卖建议；总字数不超过650字，不使用【】。",
}
