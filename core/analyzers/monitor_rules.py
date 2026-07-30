"""Deterministic monitor eligibility, risk, and duplicate-detection rules."""

from __future__ import annotations

from difflib import SequenceMatcher
from datetime import datetime, timedelta
import re
from typing import Any, Optional
from urllib.parse import urlparse

from config import settings
from core.monitor_store import MonitorStore


SMALL_COMPANY_NEWS_CATEGORIES = {"company"}
SMALL_COMPANY_NEWS_IMPORTANCE = {"low"}
MONITOR_ALLOWED_IMPORTANCE = {"high", "高", "偏高"}
MARKET_ALERT_DEDUP_SEVERITIES = {"重要", "紧急"}
NEWS_TRACK_CALLBACK_PREFIX = "news"
NEWS_TRACK_MINUTES = 120
_NEWS_TRACK_STOP_WORDS = {
    "news",
    "market",
    "markets",
    "report",
    "reports",
    "said",
    "says",
    "will",
    "with",
    "from",
    "that",
    "this",
    "市场",
    "消息",
    "新闻",
    "全球",
    "重要",
    "最新",
    "报道",
    "表示",
    "发布",
    "公司",
    "中国",
    "美国",
    "相关",
    "事件",
    "风险",
    "影响",
}
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
    "ecb.europa.eu",
    "bis.org",
    "hkex.com",
    "sse.com.cn",
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


def is_three_hour_market_summary_item(item: dict[str, Any]) -> bool:
    """Keep high-value market news for the three-hour market summary."""
    if item.get("discovery_only"):
        return False
    if _is_low_value_company_news(item) or not _is_monitor_alert_importance(item):
        return False

    category = str(item.get("category") or "").strip().lower()
    scope = str(item.get("market_scope") or "").strip()
    text = f"{item.get('title', '')} {item.get('digest', '')}"
    is_major_company_event = any(
        keyword in text for keyword in SMALL_COMPANY_NEWS_HIGH_IMPACT_KEYWORDS
    )
    if (
        category != "company"
        or scope not in {"", "公司", "其他"}
        or is_major_company_event
    ):
        return True
    return False


def _news_alert_severity(item: dict[str, Any]) -> Optional[str]:
    """Classify only hard market risks for minute-level delivery."""
    if item.get("discovery_only"):
        return None
    return _black_swan_alert_severity(item)


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
