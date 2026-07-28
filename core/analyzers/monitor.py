"""Rule-based news and watchlist monitor implementation."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional
from urllib.parse import urlparse

from config import settings
from core.data_fetcher import get_data_source_health, get_news, get_stock_quote
from core.monitor_store import MonitorStore, news_event_key
from utils.notifier import log_info


SMALL_COMPANY_NEWS_CATEGORIES = {"company"}
SMALL_COMPANY_NEWS_IMPORTANCE = {"low"}
MONITOR_ALLOWED_IMPORTANCE = {"high", "高", "偏高"}
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

    category = str(item.get("category") or "其他")
    sectors = _related_sector_text(item)
    if category in {"policy", "macro"}:
        transmission = "政策或宏观变化通常先影响预期、利率与风险偏好，再传导至估值和资金配置。"
    elif category in {"capital_flow", "market_sentiment"}:
        transmission = "资金与情绪变化会先反映在成交、估值和风险偏好，需避免把短时波动当作趋势。"
    else:
        transmission = "影响需要结合事件覆盖范围、产业链位置与资金反应判断，单条新闻不足以确认持续性。"
    return "\n".join(
        (
            "结构化推演如下：",
            "【确认度】该消息达到重要性阈值，但影响范围仍需由后续信息验证。",
            f"【传导路径】{transmission}",
            f"【A股映射】优先观察{sectors}与上下游的价格、成交和资金是否同步确认。",
            "【后续验证】核对正式文件、数据口径和相关公司公告，并观察次轮新闻是否补充关键细节。",
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
    for item, severity in eligible_news:
        if sent_news >= 3:
            break
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
            else f"news_sent={sent_news}, quote_samples={quote_count}, price_sent={sent_price}"
        ),
    )
