from __future__ import annotations

import re
from datetime import datetime
from typing import Any

CATEGORY_LABELS: dict[str, str] = {
    "macro": "宏观",
    "policy": "政策",
    "industry": "行业",
    "company": "公司",
    "capital_flow": "资金",
    "overseas": "海外",
    "market_sentiment": "情绪",
    "other": "其他",
}

IMPORTANCE_LABELS: dict[str, str] = {"high": "高", "medium": "中", "low": "低"}
WEEKDAY_NAMES: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _soften_trading_language(text: Any) -> str:
    softened = str(text or "").strip()
    replacements = {"买入": "关注", "卖出": "降低关注", "满仓": "高风险集中关注", "梭哈": "高风险集中关注"}
    for raw, replacement in replacements.items():
        softened = softened.replace(raw, replacement)
    return softened


def _clean_message_text(text: Any) -> str:
    """Keep generated content readable even when an older prompt uses report labels."""
    clean = _soften_trading_language(text).strip()
    clean = re.sub(r"(?m)^【([^】]+)】\s*", r"\1：", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean


def _is_display_value(value: Any) -> bool:
    return str(value or "").strip() not in {"", "未知", "其他", "见上方摘要"}


def _format_news_time(item: dict[str, Any]) -> str:
    news_time = item.get("datetime")
    if hasattr(news_time, "strftime"):
        return news_time.strftime("%Y-%m-%d %H:%M")
    return str(item.get("time_str") or "未知")


def _display_category(value: Any) -> str:
    text = str(value or "other").strip()
    return CATEGORY_LABELS.get(text, text or "其他")


def _display_importance(value: Any) -> str:
    text = str(value or "medium").strip()
    return IMPORTANCE_LABELS.get(text, text or "中")


def _format_related_sectors(value: Any) -> str:
    if isinstance(value, list):
        sectors = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(sectors[:6]) if sectors else "其他"
    text = str(value or "").strip()
    return text or "其他"


def _format_news_prompt_line(item: dict[str, Any], include_time: bool = True) -> str:
    source = str(item.get("source") or "unknown")
    time_part = f" {item.get('time_str', '')}" if include_time else ""
    tags = (
        f"分类:{_display_category(item.get('category'))} / "
        f"重要性:{_display_importance(item.get('importance'))} / "
        f"范围:{item.get('market_scope') or '其他'}"
    )
    sectors = _format_related_sectors(item.get("related_sectors"))
    return f"- [{source}]{time_part} [{tags} / 板块:{sectors}] {item.get('title', '')}"


def _format_news_facts(
    news: list[dict[str, Any]], *, limit: int = 5, include_time: bool = True
) -> str:
    """Render source-attributed headlines as facts, without adding market judgment."""
    facts: list[str] = []
    for item in news[:limit]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        source = str(item.get("source") or "未知来源").strip()
        time_text = str(item.get("time_str") or "").strip()
        time_prefix = f"{time_text}｜" if include_time and time_text else ""
        facts.append(f"{len(facts) + 1}. [{time_prefix}{source}] {title}")
    return "\n".join(facts) if facts else "未获取到可核对的新闻事实。"


def _format_sources(news: list[dict[str, Any]], fallback: str = "未知") -> str:
    sources: list[str] = []
    for item in news:
        source = str(item.get("source") or "").strip()
        if source and source not in sources:
            sources.append(source)
    return " / ".join(sources[:4]) if sources else fallback


def _format_links(links: list[Any], max_links: int = 5) -> str:
    unique_links: list[str] = []
    for link in links:
        text = str(link or "").strip()
        if text and text not in unique_links:
            unique_links.append(text)
    return "\n".join(unique_links[:max_links]) if unique_links else "未知"


def _format_source_health_line(name: str, state: dict[str, Any]) -> str:
    status = str(state.get("status") or "unknown")
    detail = str(state.get("detail") or "").strip()
    count = state.get("count")
    if status == "success":
        if count == 0:
            return f"- {name}：成功，但返回 0 条"
        if count is not None:
            return f"- {name}：成功，返回 {count} 条"
        return f"- {name}：成功"
    if status == "partial":
        count_text = f"，返回 {count} 条" if count is not None else ""
        return f"- {name}：部分失败{count_text}，{detail or '请检查数据源'}"
    if status == "skipped":
        return f"- {name}：{detail or '未调用'}"
    if status == "empty":
        return f"- {name}：返回空内容"
    if status == "failed":
        return f"- {name}：失败，{detail or '请检查数据源'}"
    return f"- {name}：{detail or status}"


def _format_weekday(moment: datetime) -> str:
    return WEEKDAY_NAMES[moment.weekday()]


def _infer_news_category(item: dict[str, Any]) -> str:
    if item.get("category"):
        return _display_category(item.get("category"))
    text = f"{item.get('title', '')} {item.get('digest', '')}"
    if any(k in text for k in ("政策", "监管", "国务院", "央行", "证监会")):
        return "政策"
    if any(k in text for k in ("资金", "主力", "流入", "流出", "融资")):
        return "资金"
    if any(k in text for k in ("美股", "海外", "全球", "Reuters", "reuters")):
        return "海外"
    if any(k in text for k in ("公司", "业绩", "公告", "增持", "减持", "回购")):
        return "公司"
    if any(k in text for k in ("行业", "板块", "产业", "AI", "芯片", "算力")):
        return "行业"
    if any(k in text for k in ("降息", "通胀", "汇率", "宏观")):
        return "宏观"
    return "其他"


def _infer_market_importance(item: dict[str, Any]) -> str:
    if item.get("importance"):
        return _display_importance(item.get("importance"))
    text = f"{item.get('title', '')} {item.get('digest', '')}"
    market_keywords = ("国务院", "央行", "证监会", "财政部", "发改委", "降息", "加息", "降准", "关税", "汇率", "人民币", "美联储", "CPI", "PPI", "通胀", "油价", "指数", "A股", "市场")
    sector_keywords = ("政策", "行业", "板块", "产业", "产业链", "多家", "集体", "AI", "算力", "芯片", "半导体", "新能源", "机器人", "医药", "地产", "银行", "券商", "消费", "军工")
    single_company_keywords = ("公告", "业绩", "回购", "增持", "减持", "股东", "签订", "中标")
    if any(k in text for k in market_keywords):
        return "高（市场级）"
    if any(k in text for k in sector_keywords):
        return "中（板块级）"
    if any(k in text for k in single_company_keywords):
        return "低（个股级）"
    return "中（待确认板块影响）"


def _title_icon(title: str) -> str:
    for keyword, icon in (("美股", "🇺🇸"), ("资金", "💰"), ("国际", "🌍"), ("宏观", "🌍"), ("三小时", "🧭"), ("市场总结", "🧭"), ("每日复盘", "🌇"), ("盘前", "☀️"), ("盘中茶歇", "🍵"), ("自选股", "📈"), ("实时监控", "⚠️"), ("市场信息", "📰"), ("市场观察", "🔎"), ("观察标的", "👀"), ("复盘辅助", "🧾")):
        if keyword in title:
            return icon
    return "📌"


def _format_market_message(title: str, *, report_time: str, source: str, category: str, importance: str, summary: str, impact: str = "见上方摘要", links: str = "未知", market_scope: str = "其他", related_sectors: Any = None, include_title: bool = True) -> str:
    """Format Telegram content as a brief, not a field-by-field report.

    ``category`` and ``importance`` remain accepted because callers use them for
    classification, but they are intentionally not repeated to readers. Urgency
    belongs in the message title; ordinary messages should lead with the useful
    information instead of metadata.
    """
    display_title = str(title or "市场更新").strip()
    icon = _title_icon(display_title)
    heading = display_title if display_title.startswith(icon) else f"{icon} {display_title}"
    if report_time:
        heading = f"{heading} · {report_time}"

    parts: list[str] = [heading] if include_title else []
    context: list[str] = []
    if _is_display_value(source):
        context.append(f"来源：{source}")
    sectors = _format_related_sectors(related_sectors)
    if _is_display_value(sectors):
        context.append(f"涉及：{sectors}")
    if context:
        parts.append(" · ".join(context))

    clean_summary = _clean_message_text(summary)
    if clean_summary:
        parts.append(clean_summary)

    clean_impact = _clean_message_text(impact)
    if _is_display_value(clean_impact):
        parts.append(f"怎么看\n{clean_impact}")

    clean_links = _clean_message_text(links)
    if _is_display_value(clean_links):
        parts.append(f"原文\n{clean_links}")

    return "\n\n".join(parts)
