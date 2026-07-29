"""Rule-based news and watchlist monitor implementation."""

from __future__ import annotations

from difflib import SequenceMatcher
from datetime import datetime, time, timedelta
import re
from typing import Any, Optional
from urllib.parse import urlparse

from config import settings
from core.data_fetcher import get_data_source_health, get_news, get_stock_quote
from core.monitor_store import MonitorStore, news_event_key
from utils.notifier import log_info


SMALL_COMPANY_NEWS_CATEGORIES = {"company"}
SMALL_COMPANY_NEWS_IMPORTANCE = {"low"}
MONITOR_ALLOWED_IMPORTANCE = {"high", "高", "偏高"}
MARKET_ALERT_DEDUP_SEVERITIES = {"重要", "紧急"}
BLACK_SWAN_EVENT_KEYWORDS = (
    "战争爆发",
    "宣布开战",
    "正式开战",
    "军事冲突升级",
    "爆发军事冲突",
    "导弹袭击",
    "导弹打击",
    "空袭",
    "军事打击",
    "航道封锁",
    "海峡封锁",
    "全面封锁",
    "航道中断",
    "油气供应中断",
    "恐怖袭击",
    "政变",
    "戒严",
    "紧急状态",
    "核泄漏",
    "核事故",
    "核设施遇袭",
    "核设施受袭",
    "核设施遭袭",
    "金融危机",
    "流动性危机",
    "银行挤兑",
    "银行倒闭",
    "银行接管",
    "主权违约",
    "债务违约",
    "市场熔断",
    "交易熔断",
    "股灾",
    "闪崩",
    "交易所宕机",
    "交易中断",
    "交易所暂停交易",
    "证券交易暂停",
    "强震",
    "海啸",
    "大规模疫情",
    "网络攻击",
    "网络袭击",
    "勒索软件",
    "支付系统故障",
    "支付系统中断",
    "清算系统故障",
    "大面积停电",
    "电网故障",
    "制裁升级",
    "全面制裁",
    "禁运",
    "出口管制",
    "资本管制",
    "汇率崩盘",
    "汇率暴跌",
    "外汇管制",
    "war breaks out",
    "declared war",
    "military strike",
    "missile strike",
    "airstrike",
    "blockade",
    "terror attack",
    "coup",
    "martial law",
    "state of emergency",
    "nuclear leak",
    "nuclear accident",
    "nuclear facility attack",
    "financial crisis",
    "liquidity crisis",
    "bank run",
    "bank collapse",
    "bank resolution",
    "sovereign default",
    "debt default",
    "market crash",
    "circuit breaker",
    "exchange outage",
    "trading halt",
    "major earthquake",
    "powerful earthquake",
    "tsunami",
    "pandemic",
    "cyberattack",
    "ransomware attack",
    "payment system outage",
    "clearing system outage",
    "grid outage",
    "sanctions escalation",
    "trade embargo",
    "export controls",
    "capital controls",
    "currency crash",
    "shipping lane disruption",
    "oil supply disruption",
)
BLACK_SWAN_STRONG_PHRASES = (
    "正式开战",
    "宣布开战",
    "进入紧急状态",
    "触发熔断",
    "发生强震",
    "发生海啸",
    "宣布破产",
    "主权违约",
    "核事故",
    "核泄漏",
    "支付系统中断",
    "清算系统故障",
    "全面制裁",
    "资本管制",
    "bank run",
    "declared war",
    "state of emergency",
    "circuit breaker triggered",
    "nuclear accident",
    "payment system outage",
    "capital controls",
)
BLACK_SWAN_CONTEXT_EXCLUSIONS = (
    "历史回顾",
    "周年纪念",
    "军事演习",
    "模拟演练",
    "电影",
    "电视剧",
    "游戏",
    "小说",
    "假设",
    "虚构",
    "historical review",
    "anniversary",
    "military drill",
    "military exercise",
    "simulation",
    "simulated",
    "movie",
    "film",
    "television",
    "tv series",
    "game",
    "novel",
    "fictional",
    "hypothetical",
)
BLACK_SWAN_UNVERIFIED_TERMS = (
    "未经证实",
    "网传",
    "传闻",
    "尚未证实",
    "unconfirmed",
    "unverified",
    "rumor",
)
TRUSTED_URGENT_SOURCE_NAMES = (
    "eastmoney",
    "reuters",
    "associated press",
    "ap",
    "bbc",
    "financial times",
    "ft",
    "wall street journal",
    "wsj",
    "bloomberg",
)
TRUSTED_URGENT_SOURCE_HOSTS = (
    "eastmoney.com",
    "reuters.com",
    "reutersagency.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "ft.com",
    "wsj.com",
    "bloomberg.com",
)
SMALL_COMPANY_NEWS_HIGH_IMPACT_KEYWORDS = (
    "停牌",
    "复牌",
    "并购",
    "重组",
    "退市",
    "立案",
    "证监会",
    "重大资产",
    "控制权",
    "暴雷",
)
MILITARY_RISK_TERMS = (
    "战争爆发",
    "宣布开战",
    "正式开战",
    "军事冲突升级",
    "爆发军事冲突",
    "导弹袭击",
    "导弹打击",
    "空袭",
    "军事打击",
    "航道封锁",
    "海峡封锁",
    "全面封锁",
    "恐怖袭击",
    "政变",
    "戒严",
    "war breaks out",
    "declared war",
    "military strike",
    "missile strike",
    "airstrike",
    "blockade",
    "terror attack",
    "coup",
    "martial law",
)
MARKET_INFRASTRUCTURE_RISK_TERMS = (
    "交易所宕机",
    "交易中断",
    "交易所暂停交易",
    "证券交易暂停",
    "网络攻击",
    "网络袭击",
    "勒索软件",
    "支付系统故障",
    "支付系统中断",
    "清算系统故障",
    "大面积停电",
    "电网故障",
    "exchange outage",
    "trading halt",
    "cyberattack",
    "ransomware attack",
    "payment system outage",
    "clearing system outage",
    "grid outage",
)
FINANCIAL_RISK_TERMS = (
    "金融危机",
    "流动性危机",
    "银行挤兑",
    "银行倒闭",
    "银行接管",
    "主权违约",
    "债务违约",
    "市场熔断",
    "交易熔断",
    "股灾",
    "闪崩",
    "financial crisis",
    "liquidity crisis",
    "bank run",
    "bank collapse",
    "bank resolution",
    "sovereign default",
    "debt default",
    "market crash",
    "circuit breaker",
)
SANCTIONS_RISK_TERMS = (
    "制裁升级",
    "全面制裁",
    "禁运",
    "出口管制",
    "资本管制",
    "汇率崩盘",
    "汇率暴跌",
    "外汇管制",
    "sanctions escalation",
    "trade embargo",
    "export controls",
    "capital controls",
    "currency crash",
)
ENERGY_SUPPLY_RISK_TERMS = (
    "航道中断",
    "油气供应中断",
    "shipping lane disruption",
    "oil supply disruption",
)
NATURAL_DISASTER_RISK_TERMS = (
    "核泄漏",
    "核事故",
    "核设施遇袭",
    "核设施受袭",
    "核设施遭袭",
    "强震",
    "海啸",
    "大规模疫情",
    "nuclear leak",
    "nuclear accident",
    "nuclear facility attack",
    "major earthquake",
    "powerful earthquake",
    "tsunami",
    "pandemic",
)
MONETARY_POLICY_TERMS = (
    "央行",
    "降准",
    "降息",
    "加息",
    "lpr",
    "mlf",
    "逆回购",
    "公开市场操作",
    "存款准备金",
    "货币政策",
)
FISCAL_POLICY_TERMS = (
    "财政部",
    "财政政策",
    "专项债",
    "特别国债",
    "政府债",
    "预算",
    "税收",
    "补贴",
    "消费券",
    "以旧换新",
)
CAPITAL_MARKET_POLICY_TERMS = (
    "证监会",
    "交易所",
    "ipo",
    "上市审核",
    "退市",
    "融资融券",
    "程序化交易",
    "分红",
    "回购",
    "减持",
)
TRADE_POLICY_TERMS = (
    "关税",
    "出口",
    "进口",
    "贸易",
    "商务部",
    "配额",
    "反倾销",
    "反补贴",
)
GROWTH_DATA_TERMS = (
    "gdp",
    "pmi",
    "就业",
    "社融",
    "信贷",
    "工业增加值",
    "零售",
    "固定资产投资",
    "经济增长",
)
INFLATION_RATE_TERMS = (
    "cpi",
    "ppi",
    "通胀",
    "通缩",
    "利率",
    "国债",
    "收益率",
)
CURRENCY_RATE_TERMS = (
    "人民币",
    "美元",
    "汇率",
    "美联储",
    "fed",
    "联邦基金利率",
)
INSTITUTIONAL_FLOW_TERMS = (
    "北向资金",
    "南向资金",
    "etf",
    "融资融券",
    "主力资金",
    "净流入",
    "净流出",
)


def _is_monitor_alert_importance(item: dict[str, Any]) -> bool:
    """Return True only for news importance levels the monitor should send."""
    importance = str(item.get("importance") or "").strip().lower()
    return importance in MONITOR_ALLOWED_IMPORTANCE


def _contains_risk_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _is_unverified_black_swan(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    return _contains_risk_term(text, BLACK_SWAN_UNVERIFIED_TERMS)


def _has_excluded_black_swan_context(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    return _contains_risk_term(text, BLACK_SWAN_CONTEXT_EXCLUSIONS)


def _black_swan_score(item: dict[str, Any]) -> int:
    """Score severity evidence after event and context checks have passed."""
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    score = 2
    if _contains_risk_term(text, BLACK_SWAN_STRONG_PHRASES):
        score += 2
    if _is_trusted_urgent_source(item):
        score += 1
    if _is_monitor_alert_importance(item):
        score += 1
    return score


def _is_black_swan_candidate(item: dict[str, Any]) -> bool:
    """Require a concrete event, then score source and evidence conservatively."""
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    if not _contains_risk_term(text, BLACK_SWAN_EVENT_KEYWORDS):
        return False
    if _has_excluded_black_swan_context(item):
        return False

    score = _black_swan_score(item)
    if _is_unverified_black_swan(item):
        return _is_trusted_urgent_source(item) and score >= 4
    return score >= 3


def _is_trusted_urgent_source(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "").strip().lower()
    host = urlparse(str(item.get("link") or "")).netloc.lower()
    if source in TRUSTED_URGENT_SOURCE_NAMES:
        return True
    if host.endswith((".gov", ".gov.cn", ".gov.uk")):
        return True
    return any(
        host == trusted_host or host.endswith(f".{trusted_host}")
        for trusted_host in TRUSTED_URGENT_SOURCE_HOSTS
    )


def _black_swan_alert_severity(item: dict[str, Any]) -> Optional[str]:
    """Classify confirmed events as urgent and unverified ones as cautionary."""
    if not _is_black_swan_candidate(item):
        return None
    if _is_unverified_black_swan(item) or not _is_trusted_urgent_source(item):
        return "待核实"
    return "紧急"


def _is_low_value_company_news(item: dict[str, Any]) -> bool:
    """Return True for ordinary single-company updates that should not be pushed."""
    category = str(item.get("category") or "").strip().lower()
    importance = str(item.get("importance") or "").strip().lower()
    scope = str(item.get("market_scope") or "").strip()
    text = f"{item.get('title', '')} {item.get('digest', '')}"

    if any(keyword in text for keyword in SMALL_COMPANY_NEWS_HIGH_IMPACT_KEYWORDS):
        return False

    return (
        category in SMALL_COMPANY_NEWS_CATEGORIES
        and importance in SMALL_COMPANY_NEWS_IMPORTANCE
        and scope in {"", "公司", "其他"}
    )


def _news_alert_severity(item: dict[str, Any]) -> Optional[str]:
    """Classify only high-value news for deterministic, low-latency delivery."""
    black_swan_severity = _black_swan_alert_severity(item)
    if black_swan_severity:
        return black_swan_severity
    if _is_low_value_company_news(item) or not _is_monitor_alert_importance(item):
        return None

    category = str(item.get("category") or "").strip().lower()
    scope = str(item.get("market_scope") or "").strip()
    text = f"{item.get('title', '')} {item.get('digest', '')}"
    is_major_company_event = any(
        keyword in text for keyword in SMALL_COMPANY_NEWS_HIGH_IMPACT_KEYWORDS
    )
    if category != "company" or scope not in {"", "公司", "其他"} or is_major_company_event:
        return "重要"
    return None


def _is_news_in_alert_window(item: dict[str, Any], now: datetime) -> bool:
    """Keep normal alerts fresh while allowing a short late window for emergencies."""
    published_at = item.get("datetime")
    if not isinstance(published_at, datetime):
        return False
    if published_at >= now - timedelta(minutes=settings.MONITOR_NEWS_FRESH_MINUTES):
        return True
    return _is_black_swan_candidate(item) and published_at >= now - timedelta(
        minutes=settings.MONITOR_NEWS_LOOKBACK_MINUTES
    )


def _normalise_market_event_text(value: Any) -> str:
    """Normalise a headline or digest without using AI or source identity."""
    text = str(value or "").casefold()
    return re.sub(r"[\W_]+", "", text)


def _market_event_numbers(value: str) -> set[str]:
    """Keep material numbers so a numerical update is not treated as a repeat."""
    return set(re.findall(r"\d+(?:[.,]\d+)?%?", value))


def _same_market_event(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    """Return True only for conservative, cross-source duplicate market events."""
    current_link = str(current.get("link") or "").strip()
    previous_link = str(previous.get("link") or "").strip()
    if current_link and current_link == previous_link:
        return True

    current_title = _normalise_market_event_text(current.get("title"))
    previous_title = _normalise_market_event_text(previous.get("title"))
    if not current_title or not previous_title:
        return False

    current_digest = _normalise_market_event_text(current.get("digest"))
    previous_digest = _normalise_market_event_text(previous.get("digest"))
    current_text = current_title + current_digest
    previous_text = previous_title + previous_digest
    if _market_event_numbers(current_text) != _market_event_numbers(previous_text):
        return False

    title_ratio = SequenceMatcher(None, current_title, previous_title).ratio()
    if current_title == previous_title:
        if not current_digest or not previous_digest:
            return True
        return SequenceMatcher(None, current_digest, previous_digest).ratio() >= 0.75

    combined_ratio = SequenceMatcher(None, current_text, previous_text).ratio()
    return title_ratio >= 0.92 and combined_ratio >= 0.88


def _is_recent_market_alert_duplicate(
    store: MonitorStore,
    item: dict[str, Any],
    severity: str,
    now: datetime,
) -> bool:
    """Suppress only same-severity, recently delivered important/urgent events."""
    if severity not in MARKET_ALERT_DEDUP_SEVERITIES:
        return False
    recent_payloads = store.recent_sent_alert_payloads(
        alert_type="news",
        severity=severity,
        now=now,
        lookback_minutes=settings.MONITOR_MARKET_ALERT_DEDUP_MINUTES,
    )
    return any(_same_market_event(item, payload) for payload in recent_payloads)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _normalise_watchlist_codes(raw_codes: list[str]) -> list[str]:
    """Accept six-digit A-share codes only and preserve their configured order."""
    codes: list[str] = []
    for raw_code in raw_codes:
        code = str(raw_code or "").strip()
        if not code.isdigit() or len(code) > 6:
            log_info(f"忽略无效 WATCHLIST_CODES 条目: {code or '空'}")
            continue
        code = code.zfill(6)
        if code not in codes:
            codes.append(code)
    return codes


def _is_a_share_trading_session(now: datetime) -> bool:
    """Avoid treating closed-market snapshots as fresh one-minute price changes."""
    if now.weekday() >= 5:
        return False
    current_time = now.time().replace(tzinfo=None)
    return time(9, 30) <= current_time <= time(11, 30) or time(13, 0) <= current_time <= time(15, 0)


def _claim_and_send(
    store: MonitorStore,
    *,
    alert_key: str,
    dedup_key: str,
    alert_type: str,
    severity: str,
    content: str,
    payload: dict[str, Any],
    now: datetime,
    cooldown_minutes: int = 0,
) -> bool:
    """Send only a claimed alert and leave a failed delivery retryable."""
    from core.runtime import _send_tg_with_summary

    if not store.claim_alert(
        alert_key=alert_key,
        dedup_key=dedup_key,
        alert_type=alert_type,
        severity=severity,
        payload=payload,
        now=now,
        cooldown_minutes=cooldown_minutes,
    ):
        return False

    try:
        sent = _send_tg_with_summary(
            content,
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
    except Exception as exc:
        store.mark_alert_failed(alert_key, now, exc.__class__.__name__)
        raise

    if sent:
        store.mark_alert_sent(alert_key, now)
        return True

    store.mark_alert_failed(alert_key, now, "telegram send returned false")
    return False


def _build_news_alert(item: dict[str, Any], severity: str) -> str:
    from core.formatter import _format_market_message, _format_news_time

    is_urgent = severity == "紧急"
    is_unverified = severity == "待核实"
    if is_urgent:
        title = "紧急市场提醒"
        importance = "高（紧急）"
        impact = _build_monitor_impact(item, severity)
    elif is_unverified:
        title = "待核实风险提示"
        importance = "中（待核实）"
        impact = _build_monitor_impact(item, severity)
    else:
        title = "重要市场提醒"
        importance = "高"
        impact = _build_monitor_impact(item, severity)
    return _format_market_message(
        title,
        report_time=_format_news_time(item),
        source=str(item.get("source") or "未知"),
        category=str(item.get("category") or "其他"),
        importance=importance,
        summary=str(item.get("title") or "未知新闻"),
        impact=impact,
        links=str(item.get("link") or "未知"),
        market_scope=str(item.get("market_scope") or "其他"),
        related_sectors=item.get("related_sectors"),
    )


def _related_sector_text(item: dict[str, Any]) -> str:
    related_sectors = item.get("related_sectors")
    if not isinstance(related_sectors, list):
        return "相关板块"
    names = [str(sector).strip() for sector in related_sectors if str(sector).strip()]
    return "、".join(names[:4]) if names else "相关板块"


def _black_swan_impact_profile(text: str) -> tuple[str, str, str]:
    """Return deterministic transmission, A-share mapping, and validation points."""
    if _contains_risk_term(text, MILITARY_RISK_TERMS):
        return (
            "冲突若扩大，通常会先通过原油、航运保险和避险情绪传导，抬升跨市场风险偏好波动。",
            "可观察石油石化、黄金、军工和航运的相对反应，并留意高估值成长与出境链的风险偏好变化。",
            "核对冲突范围、关键航道是否受阻，以及主要产油国和国际组织的正式表态。",
        )
    if _contains_risk_term(text, MARKET_INFRASTRUCTURE_RISK_TERMS):
        return (
            "交易、支付或清算链路受扰会先影响市场流动性和风险定价，若持续可能放大跨市场波动。",
            "可观察金融 IT、网络安全和支付清算相关方向，同时关注金融与高换手板块是否出现流动性压力。",
            "核对故障覆盖范围、服务恢复时间、监管公告及是否存在清算或交易限制。",
        )
    if _contains_risk_term(text, FINANCIAL_RISK_TERMS):
        return (
            "风险会通过信用利差、融资成本和去杠杆预期传导，先压制整体风险偏好并关注流动性。",
            "可观察银行、券商、地产和高杠杆行业的风险反应；黄金、红利等防御方向是否走强需结合行情确认。",
            "核对受影响机构、流动性支持措施、信用利差和主要市场是否出现持续异常。",
        )
    if _contains_risk_term(text, SANCTIONS_RISK_TERMS):
        return (
            "影响通常经供应链可得性、出口收入、进口成本和汇率预期传导，具体强度取决于限制范围。",
            "可观察半导体与出口链、航运物流、能源及战略资源方向；不同行业的影响取决于豁免和替代来源。",
            "核对制裁对象、生效日期、豁免条款、对手方回应及企业公告，而非只依据标题判断。",
        )
    if _contains_risk_term(text, ENERGY_SUPPLY_RISK_TERMS):
        return (
            "航道或油气供应受扰可能推升运价、保险和能源成本，并通过通胀预期影响风险资产。",
            "可观察油气、航运与资源品的相对反应，同时留意化工、航空和运输等成本敏感行业。",
            "核对实际中断时长、库存与替代运力，以及油价和运价是否同步出现持续变化。",
        )
    if _contains_risk_term(text, NATURAL_DISASTER_RISK_TERMS):
        return (
            "灾害或核安全事件会通过停产、基础设施受损和避险情绪传导，影响取决于地点与持续时间。",
            "可观察受损地区产业链、应急保障及资源品反应；板块映射须以实际受损范围和官方统计为准。",
            "核对官方伤损和停产数据、基础设施恢复进度，以及是否涉及关键产能或运输节点。",
        )
    return (
        "该事件可能先影响跨市场风险偏好和资金定价，后续强度取决于事实确认与政策响应。",
        "可结合已识别的相关板块与当日资金、价格表现观察，避免仅凭单条消息推断市场方向。",
        "优先核对权威原文、后续公告和跨市场价格是否出现同向确认。",
    )


def _important_market_impact_profile(
    item: dict[str, Any], text: str
) -> tuple[str, str, str]:
    """Explain important news with category-specific, falsifiable market paths."""
    category = str(item.get("category") or "other").strip().lower()
    sectors = _related_sector_text(item)

    if category == "policy":
        if _contains_risk_term(text, MONETARY_POLICY_TERMS):
            return (
                "政策变化通常先通过资金面、无风险利率和融资成本传导，再影响估值与风险偏好。",
                "可观察金融、地产和对估值较敏感行业的相对表现；方向仍取决于政策力度与市场原有预期的差异。",
                "核对正式文件、工具期限与规模，并观察资金利率、国债收益率和成交是否出现持续变化。",
            )
        if _contains_risk_term(text, FISCAL_POLICY_TERMS):
            return (
                "财政支持通常经政府支出、项目开工和终端需求传导，影响节奏取决于资金到位与执行进度。",
                f"可结合{sectors}及其上下游的订单、价格和开工数据观察，避免把政策标题直接等同于业绩兑现。",
                "核对资金来源、支持对象、落地时间和地方执行细则，并等待高频数据或公司公告验证。",
            )
        if _contains_risk_term(text, CAPITAL_MARKET_POLICY_TERMS):
            return (
                "资本市场制度调整会先改变交易、融资或估值预期，是否形成持续影响取决于具体规则与实施范围。",
                "可观察券商、金融 IT 和受规则直接约束的板块，同时关注成交、风险偏好与资金结构是否同步变化。",
                "核对监管原文、适用范围、生效日期及配套细则，不以媒体标题替代正式规则。",
            )
        if _contains_risk_term(text, TRADE_POLICY_TERMS):
            return (
                "贸易政策会通过订单可得性、进口成本和供应链替代传导，影响强弱取决于对象、税率与豁免范围。",
                f"可观察{sectors}及出口链、物流链的订单和价格反应；不同公司受影响程度可能明显不同。",
                "核对政策对象、生效日期、豁免条款与对手方回应，并关注企业对订单和成本的正式披露。",
            )
        return (
            "政策信息通常先改变预期和资源配置，实际影响取决于支持范围、执行节奏及是否超出市场原有预期。",
            f"可观察{sectors}与上下游的成交、价格和资金反应，不把单条政策新闻直接视为行业趋势。",
            "核对正式文件、主管部门解读和实施细则，并用后续数据验证传导是否发生。",
        )

    if category == "macro":
        if _contains_risk_term(text, GROWTH_DATA_TERMS):
            return (
                "增长与需求数据会先修正盈利和风险偏好预期，市场反应通常取决于数据与一致预期的差异。",
                f"可观察{sectors}与顺周期方向的相对表现，同时关注数据改善是否扩散至订单、库存和价格。",
                "核对同比、环比、季调口径及预期差，并等待后续月度数据确认而非只看单次读数。",
            )
        if _contains_risk_term(text, INFLATION_RATE_TERMS):
            return (
                "通胀、利率和收益率变化会通过贴现率、融资成本与利润率预期影响资产定价。",
                "可观察金融、资源品与估值敏感行业的分化；市场方向应结合利率曲线和风险偏好共同判断。",
                "核对核心与总量数据、分项来源及市场预期差，并观察债券收益率和汇率是否同向确认。",
            )
        if _contains_risk_term(text, CURRENCY_RATE_TERMS):
            return (
                "汇率和海外利率预期会通过跨境资金、进口成本与外币负债影响风险偏好和行业利润预期。",
                "可观察金融、出口链、资源品及外币负债较高行业的相对反应，但需区分短期波动和经营影响。",
                "核对官方定价、利率路径和跨境资金数据，并观察汇率与债券、股票市场是否持续联动。",
            )
        return (
            "宏观信息会通过盈利预期、利率与风险偏好传导，持续性取决于数据趋势及政策响应。",
            f"可观察{sectors}与市场风格的相对变化，并结合利率、汇率和成交确认是否出现跨资产共振。",
            "核对数据口径、预期差与后续修订，并避免用单一指标推断完整经济趋势。",
        )

    if category == "capital_flow":
        if _contains_risk_term(text, INSTITUTIONAL_FLOW_TERMS):
            return (
                "机构资金流会先反映在成交结构和相对强弱，能否形成趋势仍取决于后续资金持续性和基本面配合。",
                f"可观察{sectors}的净流入延续、成交放大和指数相对表现，避免把单日资金变化当作确定趋势。",
                "核对资金来源、连续性和成交占比，并与估值、政策或业绩催化交叉验证。",
            )
        return (
            "资金数据主要影响短期交易结构与风险偏好，持续影响需要由成交和后续配置行为确认。",
            f"可观察{sectors}的资金、价格和成交是否同步，而非只根据单一流入流出指标判断。",
            "核对统计口径、时间窗口和资金来源，并关注次日及后续交易日是否延续。",
        )

    if category == "market_sentiment":
        return (
            "情绪与指数波动会先体现在成交、波动率和风格切换，持续性取决于是否有基本面或政策信息配合。",
            f"可观察{sectors}与高波动方向的相对强弱，并留意市场广度、成交和资金是否同步改善或恶化。",
            "核对上涨或下跌家数、成交额、主要指数与北向或 ETF 数据，避免把盘中波动视为趋势确认。",
        )

    if category == "industry":
        return (
            "行业事件通常通过供需、价格、产能、技术迭代或订单预期传导，影响范围取决于产业链位置和兑现节奏。",
            f"可观察{sectors}及上下游的价格、订单、库存和资本开支变化；个别公司消息不自动代表全行业。",
            "核对事件覆盖范围、供需数据、价格指标和公司公告，并等待多来源信息相互印证。",
        )

    if category == "company":
        return (
            "重大公司事件会先影响公司自身估值与预期，只有在行业地位、交易规模或示范效应足够大时才可能外溢至板块。",
            f"可观察{sectors}及可比公司的相对表现，但不把单一公司的公告直接等同于行业趋势。",
            "核对交易条款、审批条件、财务影响和公司公告，并关注同业是否出现独立的确认信号。",
        )

    if category == "overseas":
        return (
            "海外事件会通过全球利率、汇率、大宗商品和风险偏好传导，A 股影响取决于中国资产与该变量的实际关联。",
            f"可观察{sectors}及跨境定价相关方向，同时结合人民币、利率和商品价格判断传导是否落地。",
            "核对权威原文、海外市场收盘反应和关键价格变量，避免仅依据单一海外标题推断本地市场影响。",
        )

    return (
        "该消息可能通过预期、资金或产业链影响市场，但影响范围与持续性仍需由更多事实和价格信号确认。",
        f"可观察{sectors}与上下游的相对表现，并区分个别事件与广泛市场变化。",
        "核对原始来源、正式数据和后续公告，并观察是否出现跨市场或多来源的同向确认。",
    )


def _build_monitor_impact(item: dict[str, Any], severity: str) -> str:
    """Build a fast, fact-separated impact explanation without AI latency."""
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    if severity == "待核实":
        return "\n".join(
            (
                "结构化推演如下：",
                "【确认度】该事件尚未获得充分可信确认，仅作为风险线索，不作为已发生事实。",
                "【传导路径】若后续证实，可能通过风险偏好、流动性或供应链预期影响市场。",
                "【后续验证】优先等待权威来源、监管或相关机构的第二次确认，并核对是否有跨市场价格响应。",
            )
        )

    if severity == "紧急":
        transmission, mapping, verification = _black_swan_impact_profile(text)
        return "\n".join(
            (
                "结构化推演如下：",
                "【确认度】已达到紧急事件、来源和重要性阈值；仍应以权威原文与后续公告为准。",
                f"【传导路径】{transmission}",
                f"【A股映射】{mapping}",
                f"【后续验证】{verification}",
            )
        )

    transmission, mapping, verification = _important_market_impact_profile(item, text)
    return "\n".join(
        (
            "结构化推演如下：",
            "【确认度】该消息达到重要性阈值，但影响范围仍需由后续信息验证。",
            f"【传导路径】{transmission}",
            f"【A股映射】{mapping}",
            f"【后续验证】{verification}",
        )
    )


def _build_price_alert(
    *,
    code: str,
    name: str,
    previous: dict[str, Any],
    current_price: float,
    day_pct: Optional[float],
    change_pct: float,
    now: datetime,
) -> str:
    from core.formatter import _format_market_message

    direction = "快速上涨" if change_pct > 0 else "快速下跌"
    day_pct_text = f"{day_pct:+.2f}%" if day_pct is not None else "未知"
    return _format_market_message(
        "自选股分钟异动",
        report_time=now.strftime("%Y-%m-%d %H:%M"),
        source="东方财富实时行情",
        category="行情",
        importance="高",
        summary=(
            f"{name} ({code}) {direction}："
            f"{float(previous['price']):.2f} → {current_price:.2f}，"
            f"区间变动 {change_pct:+.2f}%；当日涨跌 {day_pct_text}。"
        ),
        impact=(
            f"触发 {settings.PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES} 分钟内 "
            f"{settings.PRICE_ALERT_MINUTE_CHANGE_PCT:.2f}% 的价格异动阈值。"
            "这只是行情变化提示，不构成交易建议。"
        ),
        links="未知",
        market_scope="个股",
        related_sectors=[name],
    )


def _send_monitor_health_alert(
    store: MonitorStore, reason: str, now: datetime
) -> bool:
    """Report an actual data failure at a limited cadence instead of every minute."""
    from core.formatter import _format_market_message, _format_source_health_line
    from core.runtime import _format_health_status_message, _set_run_reason

    _set_run_reason(reason, status="failed")
    health_details = _format_health_status_message(reason, _format_source_health_line)
    content = _format_market_message(
        "实时监控状态",
        report_time=now.strftime("%Y-%m-%d %H:%M"),
        source="监控数据源",
        category="系统",
        importance="高",
        summary="实时新闻抓取没有返回可用内容。",
        impact=health_details,
        links="未知",
        market_scope="系统",
    )
    bucket = now.strftime("%Y%m%d%H") + str(now.minute // 15)
    return _claim_and_send(
        store,
        alert_key=f"health:news-fetch:{bucket}",
        dedup_key="health:news-fetch",
        alert_type="health",
        severity="high",
        content=content,
        payload={"reason": reason, "health": get_data_source_health()},
        now=now,
        cooldown_minutes=15,
    )


def _run_watchlist_monitor(store: MonitorStore, now: datetime) -> tuple[int, int]:
    """Store minute quotes and send rate-limited alerts for large short-term moves."""
    codes = _normalise_watchlist_codes(settings.WATCHLIST_CODES)
    if not codes:
        log_info("行情监控跳过：未配置 WATCHLIST_CODES")
        return 0, 0
    if not _is_a_share_trading_session(now):
        log_info("行情监控跳过：当前不在 A 股常规交易时段")
        return 0, 0

    quote_count = 0
    signal_count = 0
    for code in codes:
        quote = get_stock_quote(code)
        if not quote:
            continue
        price = _safe_float(quote.get("price"))
        day_pct = _safe_float(quote.get("pct"))
        if price is None or price <= 0:
            log_info(f"行情监控跳过无效价格: {code}")
            continue

        quote_count += 1
        name = str(quote.get("name") or code)
        previous = store.record_quote(
            code=code,
            name=name,
            price=price,
            pct=day_pct,
            observed_at=now,
            max_gap_minutes=settings.PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES,
        )
        if not previous or float(previous["price"]) <= 0:
            continue

        change_pct = (price / float(previous["price"]) - 1) * 100
        if abs(change_pct) < settings.PRICE_ALERT_MINUTE_CHANGE_PCT:
            continue

        direction = "up" if change_pct > 0 else "down"
        alert_key = f"price:{code}:{now.strftime('%Y%m%d%H%M')}:{direction}"
        if _claim_and_send(
            store,
            alert_key=alert_key,
            dedup_key=f"price:{code}:{direction}",
            alert_type="price_move",
            severity="high",
            content=_build_price_alert(
                code=code,
                name=name,
                previous=previous,
                current_price=price,
                day_pct=day_pct,
                change_pct=change_pct,
                now=now,
            ),
            payload={
                "code": code,
                "name": name,
                "previous": previous,
                "current_price": price,
                "day_pct": day_pct,
                "change_pct": change_pct,
            },
            now=now,
            cooldown_minutes=settings.PRICE_ALERT_COOLDOWN_MINUTES,
        ):
            signal_count += 1

    return quote_count, signal_count


def run_monitor(_prompts: dict[str, str]) -> None:
    """Run one minute-monitor cycle for news and configured watchlist quotes."""
    now = datetime.now(settings.SHA_TZ)
    store = MonitorStore(settings.MONITOR_DB_FILE)
    store.initialize()
    if not store.acquire_lock("monitor", now):
        log_info("实时监控跳过：上一轮尚未结束")
        return

    try:
        _run_monitor_cycle(store, now)
    finally:
        store.release_lock("monitor")


def _run_monitor_cycle(store: MonitorStore, now: datetime) -> None:
    """Process one claimed monitor cycle after the overlapping-run guard succeeds."""
    from core.runtime import _print_monitor_filter_summary, _record_news_summary

    news = get_news(
        settings.MONITOR_NEWS_LOOKBACK_MINUTES,
        semantic_dedup=False,
        translate_external=False,
    )
    _record_news_summary(news)

    input_items = len(news)
    after_time_filter = 0
    eligible_news: list[tuple[dict[str, Any], str]] = []
    recorded_news = 0
    for item in news:
        if store.record_news_event(item, now):
            recorded_news += 1
        if not _is_news_in_alert_window(item, now):
            continue
        after_time_filter += 1
        severity = _news_alert_severity(item)
        if severity:
            eligible_news.append((item, severity))

    sent_news = 0
    suppressed_duplicates = 0
    for item, severity in eligible_news:
        if sent_news >= 3:
            break
        if _is_recent_market_alert_duplicate(store, item, severity, now):
            suppressed_duplicates += 1
            log_info(
                "市场提醒去重：同级别近期已发送相同或高度相似事件，跳过重复投递"
            )
            continue
        event_key = news_event_key(item)
        if _claim_and_send(
            store,
            alert_key=f"news:{event_key}",
            dedup_key=f"news:{event_key}",
            alert_type="news",
            severity=severity,
            content=_build_news_alert(item, severity),
            payload=item,
            now=now,
        ):
            sent_news += 1

    health_sent = 0
    if not news:
        health = get_data_source_health()
        if any(state.get("status") == "failed" for state in health.values()):
            health_sent = int(
                _send_monitor_health_alert(store, "新闻数据源没有返回可用内容", now)
            )
        else:
            log_info("新闻监控无新快讯，跳过推送")

    quote_count, sent_price = _run_watchlist_monitor(store, now)
    sent_total = sent_news + sent_price + health_sent
    _print_monitor_filter_summary(
        input_items=input_items,
        after_time_filter=after_time_filter,
        after_keyword_filter=len(eligible_news),
        after_dedup=recorded_news,
        final_alert_items=sent_total,
        decision="send" if sent_total else "skip",
        reason=(
            "no new eligible news or watchlist price signal"
            if not sent_total
            else (
                f"news_sent={sent_news}, news_dedup_suppressed={suppressed_duplicates}, "
                f"quote_samples={quote_count}, price_sent={sent_price}"
            )
        ),
    )
