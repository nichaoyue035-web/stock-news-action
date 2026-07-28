"""Funds-mode analyzer implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import settings
from core.data_fetcher import get_market_funds, get_news


def _as_float(value: Any) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _flow_price_signal(item: dict[str, Any]) -> str:
    flow = _as_float(item.get("flow"))
    change = _as_float(item.get("change"))
    if flow > 0 and change > 0:
        return "流入且上涨（同向确认）"
    if flow > 0 and change <= 0:
        return "资金流入但价格未确认（分歧）"
    if flow < 0 and change < 0:
        return "流出且下跌（压力确认）"
    if flow < 0 and change >= 0:
        return "资金流出但价格未走弱（分歧）"
    return "资金或价格信号不明显"


def _format_fund_line(item: dict[str, Any]) -> str:
    flow = _as_float(item.get("flow"))
    direction = "净流入" if flow >= 0 else "净流出"
    return (
        f"- {item.get('name') or '未知板块'}：{direction}{abs(flow):.2f}亿，"
        f"涨跌{item.get('change') or '未知'}，{_flow_price_signal(item)}"
    )


def _fund_market_temperature(
    incoming: list[dict[str, Any]], outgoing: list[dict[str, Any]]
) -> str:
    in_confirmed = sum(
        _as_float(item.get("flow")) > 0 and _as_float(item.get("change")) > 0
        for item in incoming
    )
    out_confirmed = sum(
        _as_float(item.get("flow")) < 0 and _as_float(item.get("change")) < 0
        for item in outgoing
    )
    divergences = sum(
        "分歧" in _flow_price_signal(item) for item in [*incoming, *outgoing]
    )
    if in_confirmed >= 2 and in_confirmed > out_confirmed:
        return "偏强：多处资金流入与价格上涨同向，需继续观察次日是否延续。"
    if out_confirmed >= 2 and out_confirmed > in_confirmed:
        return "偏弱：多处资金流出与价格下跌同向，风险偏好需要进一步确认。"
    if divergences:
        return "分歧：资金与价格存在背离，暂不把单日流向当作趋势。"
    return "中性：资金信号尚未形成清晰且可验证的主线。"


def _related_funds_news(
    news: list[dict[str, Any]], sectors: list[str]
) -> list[dict[str, Any]]:
    """Keep only news that can be tied to a leading inflow or outflow sector."""
    normalized_sectors = [sector.lower() for sector in sectors if sector]
    related: list[dict[str, Any]] = []
    for item in news:
        tagged = [
            str(sector).lower()
            for sector in item.get("related_sectors", [])
            if str(sector).strip()
        ]
        text = f"{item.get('title', '')} {item.get('digest', '')}".lower()
        if any(sector in tagged or sector in text for sector in normalized_sectors):
            related.append(item)
        if len(related) == 3:
            break
    return related


def _format_funds_snapshot(
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
    related_news: list[dict[str, Any]],
) -> str:
    lines = [f"【资金温度】{_fund_market_temperature(incoming, outgoing)}", "【流入主线】"]
    lines.extend(_format_fund_line(item) for item in incoming)
    lines.append("【流出压力】")
    lines.extend(_format_fund_line(item) for item in outgoing)
    lines.append("【新闻催化】")
    if related_news:
        lines.extend(
            f"- [{item.get('source') or '未知来源'}] {item.get('title') or '未知新闻'}"
            for item in related_news
        )
    else:
        lines.append("- 暂未发现与上述板块直接匹配的新闻催化。")
    return "\n".join(lines)


def run_funds(prompts: dict[str, str]) -> None:
    """Run funds flow analysis mode."""
    from core.formatter import (
        _format_links,
        _format_market_message,
        _format_news_prompt_line,
        _format_sources,
    )
    from core.runtime import (
        _record_fetch_success,
        _record_news_summary,
        _send_health_status,
        _send_tg_with_summary,
    )
    from core.analyzer import (
        _get_ai_response_with_health,
    )

    now = datetime.now(settings.SHA_TZ)
    top_in, top_out = get_market_funds()
    _record_fetch_success(bool(top_in))
    if not top_in:
        _send_health_status("资金流数据为空，无法生成资金流摘要")
        return
    incoming = [item for item in top_in if _as_float(item.get("flow")) > 0][:4]
    outgoing = [item for item in top_out if _as_float(item.get("flow")) < 0][:4]
    if not incoming:
        incoming = top_in[:4]
    if not outgoing:
        outgoing = top_out[:4]
    in_str = "\n".join(_format_fund_line(item) for item in incoming)
    out_str = "\n".join(_format_fund_line(item) for item in outgoing)
    news = get_news(720)
    _record_news_summary(news)
    focus_sectors = [
        str(item.get("name") or "") for item in [*incoming, *outgoing]
    ]
    related_news = _related_funds_news(news, focus_sectors)
    news_txt = "\n".join(
        [_format_news_prompt_line(n, include_time=True) for n in related_news]
    )
    content = _get_ai_response_with_health(
        prompts.get("funds", settings.DEFAULT_PROMPTS["funds"]).format(
            in_str=in_str,
            out_str=out_str,
            news_txt=news_txt or "无重要消息",
            report_date=now.strftime("%Y-%m-%d"),
            report_time=now.strftime("%Y-%m-%d %H:%M"),
        ),
        model="deepseek-reasoner",
    )
    if content:
        message = _format_market_message(
            "主力资金雷达",
            report_time=now.strftime("%Y-%m-%d %H:%M"),
            source=_format_sources(news, "东方财富资金流 / 新闻源"),
            category="capital_flow",
            importance="medium",
            summary=_format_funds_snapshot(incoming, outgoing, related_news),
            impact=content,
            links=_format_links([item.get("link") for item in related_news]),
            market_scope="行业",
            related_sectors=[item["name"] for item in incoming[:3]],
        )
        _send_tg_with_summary(message)
    else:
        _send_health_status("DeepSeek 没有生成有效摘要")
