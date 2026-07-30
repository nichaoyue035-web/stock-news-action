"""Deterministic monitor alert messages and conditional market context."""

from __future__ import annotations

from typing import Any

from core.analyzers import monitor_rules as rules


def _build_news_alert(item: dict[str, Any], severity: str) -> str:
    from core.formatter import _format_market_message, _format_news_time

    is_urgent = severity == "紧急"
    is_unverified = severity == "待核实"
    if is_urgent or severity == "重要":
        return _format_compact_market_alert(
            item,
            severity=severity,
            report_time=_format_news_time(item),
        )
    if is_unverified:
        title = "待核实风险提示"
        importance = "中（待核实）"
        impact = _build_monitor_impact(item, severity)
    else:
        title = "紧急市场提醒"
        importance = "高（紧急）"
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


def _compact_alert_text(value: Any, limit: int = 160) -> str:
    """Keep a factual line readable without inventing a summary."""
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _compact_market_insight(item: dict[str, Any], severity: str) -> tuple[str, str]:
    """Return the one market question and the one confirmation point that matter."""
    text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
    sectors = _related_sector_text(item)

    if severity == "紧急":
        if rules._contains_risk_term(text, rules.MILITARY_RISK_TERMS):
            return (
                "风险取决于冲突是否扩大并扰乱油运。",
                "看油价、运价、黄金与军工是否同步反应；核对范围、航道和官方表态。",
            )
        if rules._contains_risk_term(text, rules.MARKET_INFRASTRUCTURE_RISK_TERMS):
            return (
                "关键在交易、支付或清算受影响的范围和恢复时间。",
                "看监管公告、是否限交，以及金融与高换手板块的流动性。",
            )
        if rules._contains_risk_term(text, rules.FINANCIAL_RISK_TERMS):
            return (
                "关键在风险是否扩散为融资与信用压力。",
                "看受影响机构、流动性支持和信用利差是否持续异常。",
            )
        if rules._contains_risk_term(text, rules.SANCTIONS_RISK_TERMS):
            return (
                "关键在限制范围、豁免和替代空间。",
                "看制裁对象、生效时间及供应链、汇率的实际反应。",
            )
        if rules._contains_risk_term(text, rules.ENERGY_SUPPLY_RISK_TERMS):
            return (
                "关键在油气或航运是否出现实际中断。",
                "看油价、运价、库存与替代运力，而不是只看标题。",
            )
        if rules._contains_risk_term(text, rules.NATURAL_DISASTER_RISK_TERMS):
            return (
                "关键在灾害是否伤及关键产能或基础设施。",
                "看官方伤损、停产与恢复进度。",
            )
        return (
            "关键在事实是否持续升级为跨市场风险。",
            "优先核对权威原文与核心价格变量的同步反应。",
        )

    category = str(item.get("category") or "other").strip().lower()
    if category == "policy":
        if rules._contains_risk_term(text, rules.MONETARY_POLICY_TERMS):
            return (
                "关键在资金与利率预期是否真正改变。",
                "看正式工具、期限和规模，以及资金利率、收益率与金融地产反应。",
            )
        if rules._contains_risk_term(text, rules.FISCAL_POLICY_TERMS):
            return (
                "关键在资金是否到位、项目能否落地。",
                f"看支持对象、落地时间及{sectors}的订单和开工。",
            )
        if rules._contains_risk_term(text, rules.CAPITAL_MARKET_POLICY_TERMS):
            return (
                "关键在规则边界和生效时间。",
                "看监管原文、券商与金融 IT 的反应，以及成交结构。",
            )
        if rules._contains_risk_term(text, rules.TRADE_POLICY_TERMS):
            return (
                "关键在对象、税率与豁免条款。",
                f"看出口链、物流与{sectors}的订单、成本变化。",
            )
        return (
            "关键在正式文件是否超出原有预期，以及落地节奏。",
            f"看细则、执行时间和{sectors}的价格、成交反应。",
        )

    if category == "macro":
        if rules._contains_risk_term(text, rules.GROWTH_DATA_TERMS):
            return (
                "关键是数据相对预期的变化，而不是单看绝对数。",
                f"看预期差、订单库存及{sectors}与顺周期方向是否确认。",
            )
        if rules._contains_risk_term(text, rules.INFLATION_RATE_TERMS):
            return (
                "关键在利率路径是否被重新定价。",
                "看核心数据、债券收益率与汇率是否同步变化。",
            )
        if rules._contains_risk_term(text, rules.CURRENCY_RATE_TERMS):
            return (
                "关键在汇率波动是否传导为跨境资金或成本压力。",
                "看汇率、利率和资金流的联动，而不是单一时点波动。",
            )
        return (
            "关键在预期差能否被后续数据验证。",
            "看数据口径、修订和利率、汇率、成交的同步反应。",
        )

    if category == "capital_flow":
        return (
            "关键在资金是否连续，而不是单日流入或流出。",
            f"看{sectors}的成交、价格与资金是否同向延续。",
        )
    if category == "market_sentiment":
        return (
            "关键在情绪是否得到成交和市场广度确认。",
            "看成交额、涨跌家数、指数与资金是否同步。",
        )
    if category == "industry":
        return (
            "关键在供需、价格或订单是否出现真实变化。",
            f"看{sectors}上下游的价格、库存和订单，而非单条新闻。",
        )
    if category == "company":
        return (
            "先看事项规模、审批条件和财务影响，不直接外推为行业趋势。",
            f"看正式公告及{sectors}、同业是否出现独立确认。",
        )
    if category == "overseas":
        return (
            "关键在海外变量是否落地到利率、汇率或商品价格。",
            f"看权威原文及{sectors}与人民币、商品价格的同步反应。",
        )
    return (
        "先确认事实范围，再判断是否改变预期或资金定价。",
        f"看原始来源和{sectors}的价格、成交是否给出确认。",
    )


def _format_compact_market_alert(
    item: dict[str, Any], *, severity: str, report_time: str
) -> str:
    """Format important and urgent alerts around only the decision-relevant facts."""
    label = "🚨 紧急" if severity == "紧急" else "🔔 重要"
    source = _compact_alert_text(item.get("source") or "未知来源", 48)
    headline = _compact_alert_text(item.get("title") or "未知事件")
    digest = _compact_alert_text(item.get("digest"))
    takeaway, watch = _compact_market_insight(item, severity)
    lines = [
        f"{label} · {report_time or '未知时间'}",
        headline,
        f"来源：{source}",
    ]
    if digest and digest != headline:
        lines.append(f"重点：{digest}")
    lines.extend((f"影响：{takeaway}", f"接着看：{watch}"))
    link = _compact_alert_text(item.get("link"), 300)
    if link:
        lines.append(f"原文：{link}")
    return "\n".join(lines)


def _related_sector_text(item: dict[str, Any]) -> str:
    related_sectors = item.get("related_sectors")
    if not isinstance(related_sectors, list):
        return "相关板块"
    names = [str(sector).strip() for sector in related_sectors if str(sector).strip()]
    return "、".join(names[:4]) if names else "相关板块"


def _black_swan_impact_profile(text: str) -> tuple[str, str, str]:
    """Return deterministic transmission, A-share mapping, and validation points."""
    if rules._contains_risk_term(text, rules.MILITARY_RISK_TERMS):
        return (
            "冲突若扩大，通常会先通过原油、航运保险和避险情绪传导，抬升跨市场风险偏好波动。",
            "可观察石油石化、黄金、军工和航运的相对反应，并留意高估值成长与出境链的风险偏好变化。",
            "核对冲突范围、关键航道是否受阻，以及主要产油国和国际组织的正式表态。",
        )
    if rules._contains_risk_term(text, rules.MARKET_INFRASTRUCTURE_RISK_TERMS):
        return (
            "交易、支付或清算链路受扰会先影响市场流动性和风险定价，若持续可能放大跨市场波动。",
            "可观察金融 IT、网络安全和支付清算相关方向，同时关注金融与高换手板块是否出现流动性压力。",
            "核对故障覆盖范围、服务恢复时间、监管公告及是否存在清算或交易限制。",
        )
    if rules._contains_risk_term(text, rules.FINANCIAL_RISK_TERMS):
        return (
            "风险会通过信用利差、融资成本和去杠杆预期传导，先压制整体风险偏好并关注流动性。",
            "可观察银行、券商、地产和高杠杆行业的风险反应；黄金、红利等防御方向是否走强需结合行情确认。",
            "核对受影响机构、流动性支持措施、信用利差和主要市场是否出现持续异常。",
        )
    if rules._contains_risk_term(text, rules.SANCTIONS_RISK_TERMS):
        return (
            "影响通常经供应链可得性、出口收入、进口成本和汇率预期传导，具体强度取决于限制范围。",
            "可观察半导体与出口链、航运物流、能源及战略资源方向；不同行业的影响取决于豁免和替代来源。",
            "核对制裁对象、生效日期、豁免条款、对手方回应及企业公告，而非只依据标题判断。",
        )
    if rules._contains_risk_term(text, rules.ENERGY_SUPPLY_RISK_TERMS):
        return (
            "航道或油气供应受扰可能推升运价、保险和能源成本，并通过通胀预期影响风险资产。",
            "可观察油气、航运与资源品的相对反应，同时留意化工、航空和运输等成本敏感行业。",
            "核对实际中断时长、库存与替代运力，以及油价和运价是否同步出现持续变化。",
        )
    if rules._contains_risk_term(text, rules.NATURAL_DISASTER_RISK_TERMS):
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
        if rules._contains_risk_term(text, rules.MONETARY_POLICY_TERMS):
            return (
                "政策变化通常先通过资金面、无风险利率和融资成本传导，再影响估值与风险偏好。",
                "可观察金融、地产和对估值较敏感行业的相对表现；方向仍取决于政策力度与市场原有预期的差异。",
                "核对正式文件、工具期限与规模，并观察资金利率、国债收益率和成交是否出现持续变化。",
            )
        if rules._contains_risk_term(text, rules.FISCAL_POLICY_TERMS):
            return (
                "财政支持通常经政府支出、项目开工和终端需求传导，影响节奏取决于资金到位与执行进度。",
                f"可结合{sectors}及其上下游的订单、价格和开工数据观察，避免把政策标题直接等同于业绩兑现。",
                "核对资金来源、支持对象、落地时间和地方执行细则，并等待高频数据或公司公告验证。",
            )
        if rules._contains_risk_term(text, rules.CAPITAL_MARKET_POLICY_TERMS):
            return (
                "资本市场制度调整会先改变交易、融资或估值预期，是否形成持续影响取决于具体规则与实施范围。",
                "可观察券商、金融 IT 和受规则直接约束的板块，同时关注成交、风险偏好与资金结构是否同步变化。",
                "核对监管原文、适用范围、生效日期及配套细则，不以媒体标题替代正式规则。",
            )
        if rules._contains_risk_term(text, rules.TRADE_POLICY_TERMS):
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
        if rules._contains_risk_term(text, rules.GROWTH_DATA_TERMS):
            return (
                "增长与需求数据会先修正盈利和风险偏好预期，市场反应通常取决于数据与一致预期的差异。",
                f"可观察{sectors}与顺周期方向的相对表现，同时关注数据改善是否扩散至订单、库存和价格。",
                "核对同比、环比、季调口径及预期差，并等待后续月度数据确认而非只看单次读数。",
            )
        if rules._contains_risk_term(text, rules.INFLATION_RATE_TERMS):
            return (
                "通胀、利率和收益率变化会通过贴现率、融资成本与利润率预期影响资产定价。",
                "可观察金融、资源品与估值敏感行业的分化；市场方向应结合利率曲线和风险偏好共同判断。",
                "核对核心与总量数据、分项来源及市场预期差，并观察债券收益率和汇率是否同向确认。",
            )
        if rules._contains_risk_term(text, rules.CURRENCY_RATE_TERMS):
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
        if rules._contains_risk_term(text, rules.INSTITUTIONAL_FLOW_TERMS):
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
                "这是风险线索，尚未充分确认，不当作已发生事实。",
                "若后续证实，才可能影响风险偏好、流动性或供应链预期。",
                "接着看：权威来源、监管或相关机构的第二次确认，以及跨市场价格反应。",
            )
        )

    if severity == "紧急":
        transmission, mapping, verification = _black_swan_impact_profile(text)
        return "\n".join(
            (
                "已达到紧急推送阈值，仍以权威原文和后续公告为准。",
                f"可能传导：{transmission}",
                f"可留意：{mapping}",
                f"接着看：{verification}",
            )
        )

    transmission, mapping, verification = _important_market_impact_profile(item, text)
    return "\n".join(
        (
            "该消息达到重要性阈值，但影响范围仍待验证。",
            f"可能传导：{transmission}",
            f"可留意：{mapping}",
            f"接着看：{verification}",
        )
    )
