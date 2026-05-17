from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional

from config import settings
from core.data_fetcher import (
    get_data_source_health,
    get_news,
    record_data_source_health,
    reset_data_source_health,
)
from utils.ai_client import get_ai_response
from utils.notifier import log_error, log_info, send_tg

HIGH_IMPACT_KEYWORDS: tuple[str, ...] = (
    "涨停",
    "跌停",
    "停牌",
    "复牌",
    "业绩",
    "并购",
    "重组",
    "回购",
    "增持",
    "减持",
    "政策",
    "降息",
    "AI",
    "算力",
    "芯片",
)

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

IMPORTANCE_LABELS: dict[str, str] = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

WEEKDAY_NAMES: tuple[str, ...] = (
    "周一",
    "周二",
    "周三",
    "周四",
    "周五",
    "周六",
    "周日",
)


CURRENT_RUN_SUMMARY: dict[str, Any] | None = None


def _start_run_summary(mode: str) -> None:
    """Initialize console-only run summary for the current execution."""
    global CURRENT_RUN_SUMMARY
    CURRENT_RUN_SUMMARY = {
        "mode": mode,
        "data_fetch_success": None,
        "news_count": None,
        "rss_count": None,
        "ai_called": False,
        "telegram_attempted": False,
        "telegram_sent": False,
        "status": None,
        "reason": "",
    }


def _get_run_summary() -> dict[str, Any] | None:
    """Return the active console-only run summary, if one exists."""
    return CURRENT_RUN_SUMMARY


def _set_run_summary(**updates: Any) -> None:
    """Update the active console-only run summary without touching Telegram content."""
    summary = _get_run_summary()
    if summary is not None:
        summary.update(updates)


def _set_run_reason(reason: str, status: str | None = None) -> None:
    """Record a concise run result reason for logs only."""
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return

    summary = _get_run_summary()
    if summary is None:
        return

    if not summary.get("reason"):
        summary["reason"] = clean_reason
    if status:
        summary["status"] = status


def _record_news_summary(news: list[dict[str, Any]]) -> None:
    """Record collected news/RSS counts for the console-only run summary."""
    health = get_data_source_health()
    rss_state = health.get("海外 RSS", {})
    rss_count = rss_state.get("count")
    summary = _get_run_summary()
    updates: dict[str, Any] = {
        "news_count": len(news),
        "rss_count": rss_count,
    }
    if summary is None or summary.get("data_fetch_success") is None:
        updates["data_fetch_success"] = bool(news)
    if news and any(
        state.get("status") in {"failed", "partial"}
        for name, state in health.items()
        if name != "DeepSeek"
    ):
        updates["status"] = "partial"
    _set_run_summary(**updates)


def _record_fetch_success(success: bool) -> None:
    """Record whether the mode's primary data fetch had usable data."""
    _set_run_summary(data_fetch_success=success)


def _derive_run_status(summary: dict[str, Any]) -> str:
    """Derive success/partial/failed without claiming false success."""
    if summary.get("status"):
        return str(summary["status"])
    if summary.get("telegram_attempted") and summary.get("telegram_sent"):
        return "success"
    if summary.get("telegram_attempted") and not summary.get("telegram_sent"):
        return "failed"
    if summary.get("data_fetch_success") is False:
        return "failed"
    if summary.get("reason"):
        return "partial"
    return "success"


def _print_run_summary() -> None:
    """Print a compact run summary to stdout/GitHub Actions logs only."""
    summary = _get_run_summary()
    if summary is None:
        return

    summary["status"] = _derive_run_status(summary)
    print("[RUN SUMMARY]")
    for key in (
        "mode",
        "data_fetch_success",
        "news_count",
        "rss_count",
        "ai_called",
        "telegram_attempted",
        "telegram_sent",
        "status",
        "reason",
    ):
        value = summary.get(key)
        if key == "reason" and not value:
            continue
        if value is None:
            value = "null"
        elif isinstance(value, bool):
            value = str(value).lower()
        print(f"{key}={value}")


def _print_monitor_filter_summary(
    *,
    input_items: int,
    after_time_filter: int,
    after_keyword_filter: int,
    after_dedup: int,
    final_alert_items: int,
    decision: str,
    reason: str = "",
) -> None:
    """Print monitor-only filter diagnostics to stdout/GitHub Actions logs."""
    print("[FILTER]", flush=True)
    print("mode=monitor", flush=True)
    print(f"input_items={input_items}", flush=True)
    print(f"after_time_filter={after_time_filter}", flush=True)
    print(f"after_keyword_filter={after_keyword_filter}", flush=True)
    print(f"after_dedup={after_dedup}", flush=True)
    print(f"final_alert_items={final_alert_items}", flush=True)
    print(f"decision={decision}", flush=True)
    if reason:
        print(f"reason={reason}", flush=True)


def _with_run_summary(mode_value: str | Callable[..., str]):
    """Decorate public modes with a console-only summary lifecycle."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            mode = mode_value(*args, **kwargs) if callable(mode_value) else mode_value
            _start_run_summary(str(mode))
            try:
                return func(*args, **kwargs)
            finally:
                _print_run_summary()

        return wrapper

    return decorator


def _send_tg_with_summary(content: Any, **kwargs: Any) -> None:
    """Send Telegram normally while tracking attempt/success in console logs only."""
    _set_run_summary(telegram_attempted=True)
    try:
        send_tg(content, **kwargs)
    except Exception as exc:
        _set_run_summary(telegram_sent=False, status="failed")
        _set_run_reason(f"telegram send failed: {exc.__class__.__name__}")
        raise
    summary = _get_run_summary() or {}
    status = "partial" if summary.get("status") == "partial" else "success"
    _set_run_summary(telegram_sent=True, status=status)

def load_prompts() -> dict[str, str]:
    """Load prompt templates from file; fallback to defaults on any error."""
    try:
        if os.path.exists(settings.PROMPTS_FILE):
            with open(settings.PROMPTS_FILE, "r", encoding="utf-8") as file:
                loaded = json.load(file)
                if isinstance(loaded, dict):
                    return loaded
                log_error("⚠️ 提示词文件格式异常: 非对象类型，将使用默认 Prompt")
    except Exception as exc:
        log_error(f"⚠️ 提示词文件读取失败: {exc}，将使用默认 Prompt")
    return settings.DEFAULT_PROMPTS


def _append_history(pick_data: dict[str, Any], start_price: str) -> None:
    """Append today's recommendation record to history CSV."""
    try:
        today_str = datetime.now(settings.SHA_TZ).strftime("%Y-%m-%d")
        file_exists = os.path.isfile(settings.HISTORY_FILE)
        with open(settings.HISTORY_FILE, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(["Date", "Name", "Code", "Start_Price", "Reason"])
            writer.writerow(
                [
                    today_str,
                    pick_data["name"],
                    pick_data["code"],
                    start_price,
                    str(pick_data["reason"]).replace("\n", " "),
                ]
            )
    except Exception as exc:
        log_error(f"❌ 历史写入失败: {exc}")


def _extract_pick_data(content: str) -> Optional[dict[str, Any]]:
    """Extract stock pick JSON object from model response text."""
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if not json_match:
        log_error("❌ AI 返回内容中未找到 JSON")
        return None

    try:
        parsed = json.loads(json_match.group())
    except json.JSONDecodeError as exc:
        log_error(f"❌ AI 返回 JSON 解析失败: {exc}")
        return None

    required_keys = ("name", "code", "reason")
    if not isinstance(parsed, dict) or any(key not in parsed for key in required_keys):
        log_error("❌ AI 返回 JSON 缺少必要字段(name/code/reason)")
        return None
    return parsed


def _safe_pct_value(raw_pct: Any) -> tuple[Optional[float], str]:
    """Convert raw pct text to numeric and normalized string for prompt/message."""
    text = str(raw_pct).replace("%", "").strip()
    try:
        pct_num = float(text)
        return pct_num, f"{pct_num:.2f}"
    except (ValueError, TypeError):
        return None, text


def _soften_trading_language(text: Any) -> str:
    """Soften direct trading words in Telegram-facing text."""
    softened = str(text or "").strip()
    replacements = {
        "买入": "关注",
        "卖出": "降低关注",
        "满仓": "高风险集中关注",
        "梭哈": "高风险集中关注",
    }
    for raw, replacement in replacements.items():
        softened = softened.replace(raw, replacement)
    return softened


def _format_news_time(item: dict[str, Any]) -> str:
    """Return a safe news timestamp for Telegram display without inventing data."""
    news_time = item.get("datetime")
    if hasattr(news_time, "strftime"):
        return news_time.strftime("%Y-%m-%d %H:%M")
    return str(item.get("time_str") or "未知")


def _display_category(value: Any) -> str:
    """Display category codes as Chinese labels, preserving custom labels."""
    text = str(value or "other").strip()
    return CATEGORY_LABELS.get(text, text or "其他")


def _display_importance(value: Any) -> str:
    """Display importance codes as Chinese labels, preserving detailed text."""
    text = str(value or "medium").strip()
    return IMPORTANCE_LABELS.get(text, text or "中")


def _format_related_sectors(value: Any) -> str:
    """Format optional sector tags without failing on legacy data."""
    if isinstance(value, list):
        sectors = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(sectors[:6]) if sectors else "其他"
    text = str(value or "").strip()
    return text or "其他"


def _format_news_prompt_line(item: dict[str, Any], include_time: bool = True) -> str:
    """Render one news item with structured tags for prompts and summaries."""
    source = str(item.get("source") or "unknown")
    time_part = f" {item.get('time_str', '')}" if include_time else ""
    tags = (
        f"分类:{_display_category(item.get('category'))} / "
        f"重要性:{_display_importance(item.get('importance'))} / "
        f"范围:{item.get('market_scope') or '其他'}"
    )
    sectors = _format_related_sectors(item.get("related_sectors"))
    return f"- [{source}]{time_part} [{tags} / 板块:{sectors}] {item.get('title', '')}"


def _format_sources(news: list[dict[str, Any]], fallback: str = "未知") -> str:
    """Format known news sources for Telegram metadata."""
    sources: list[str] = []
    for item in news:
        source = str(item.get("source") or "").strip()
        if source and source not in sources:
            sources.append(source)
    return " / ".join(sources[:4]) if sources else fallback


def _format_links(links: list[Any], max_links: int = 5) -> str:
    """Format real links only; never fabricate missing URLs."""
    unique_links: list[str] = []
    for link in links:
        text = str(link or "").strip()
        if text and text not in unique_links:
            unique_links.append(text)
    return "\n".join(unique_links[:max_links]) if unique_links else "未知"


def _format_source_health_line(name: str, state: dict[str, Any]) -> str:
    """Format one data source health record for console diagnostics."""
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


def _format_health_status_message(reason: str) -> str:
    """Build concise console-only diagnostics for no-content or failed runs."""
    health = get_data_source_health()
    if "DeepSeek" not in health:
        health["DeepSeek"] = {"status": "skipped", "detail": "未调用", "count": None}

    lines = ["数据源状态："]
    lines.extend(
        _format_source_health_line(name, state) for name, state in health.items()
    )
    if reason:
        lines.append(f"- 结果：{reason}")
    return "\n".join(lines)


def _send_health_status(
    reason: str, token: str | None = None, chat_id: str | None = None
) -> None:
    """Log health diagnostics without sending no-content Telegram messages."""
    _ = (token, chat_id)
    failure_markers = (
        "数据为空",
        "未找到",
        "无法",
        "读取失败",
        "发生异常",
        "失败",
        "正文为空",
    )
    status = (
        "failed" if any(marker in reason for marker in failure_markers) else "partial"
    )
    _set_run_reason(reason, status=status)
    log_info(_format_health_status_message(reason))


def _get_ai_response_with_health(*args, **kwargs) -> Optional[str]:
    """Call DeepSeek through the existing client and record concise health state."""
    _set_run_summary(ai_called=True)
    content = get_ai_response(*args, **kwargs)
    if str(content or "").strip():
        record_data_source_health("DeepSeek", "success", "", 1)
        return content
    record_data_source_health("DeepSeek", "empty", "返回空内容", 0)
    return None


def _has_effective_content(content: Any) -> bool:
    """Return whether a generated Telegram body has visible text."""
    return bool(str(content or "").strip())


def _format_weekday(moment: datetime) -> str:
    """Return a Chinese weekday label for Shanghai-local report context."""
    return WEEKDAY_NAMES[moment.weekday()]


def _infer_news_category(item: dict[str, Any]) -> str:
    """Infer a lightweight display category from existing news text."""
    if item.get("category"):
        return _display_category(item.get("category"))

    text = f"{item.get('title', '')} {item.get('digest', '')}"
    if any(keyword in text for keyword in ("政策", "监管", "国务院", "央行", "证监会")):
        return "政策"
    if any(keyword in text for keyword in ("资金", "主力", "流入", "流出", "融资")):
        return "资金"
    if any(
        keyword in text for keyword in ("美股", "海外", "全球", "Reuters", "reuters")
    ):
        return "海外"
    if any(
        keyword in text for keyword in ("公司", "业绩", "公告", "增持", "减持", "回购")
    ):
        return "公司"
    if any(
        keyword in text for keyword in ("行业", "板块", "产业", "AI", "芯片", "算力")
    ):
        return "行业"
    if any(keyword in text for keyword in ("降息", "通胀", "汇率", "宏观")):
        return "宏观"
    return "其他"


def _infer_market_importance(item: dict[str, Any]) -> str:
    """Estimate importance by market/sector impact, not single-company relevance."""
    if item.get("importance"):
        return _display_importance(item.get("importance"))

    text = f"{item.get('title', '')} {item.get('digest', '')}"
    market_keywords = (
        "国务院",
        "央行",
        "证监会",
        "财政部",
        "发改委",
        "降息",
        "加息",
        "降准",
        "关税",
        "汇率",
        "人民币",
        "美联储",
        "CPI",
        "PPI",
        "通胀",
        "油价",
        "指数",
        "A股",
        "市场",
    )
    sector_keywords = (
        "政策",
        "行业",
        "板块",
        "产业",
        "产业链",
        "多家",
        "集体",
        "AI",
        "算力",
        "芯片",
        "半导体",
        "新能源",
        "机器人",
        "医药",
        "地产",
        "银行",
        "券商",
        "消费",
        "军工",
    )
    single_company_keywords = (
        "公告",
        "业绩",
        "回购",
        "增持",
        "减持",
        "股东",
        "签订",
        "中标",
    )

    if any(keyword in text for keyword in market_keywords):
        return "高（市场级）"
    if any(keyword in text for keyword in sector_keywords):
        return "中（板块级）"
    if any(keyword in text for keyword in single_company_keywords):
        return "低（个股级）"
    return "中（待确认板块影响）"


def _title_icon(title: str) -> str:
    """Pick a Telegram title icon by message type instead of using one icon everywhere."""
    icon_map = (
        ("资金", "💰"),
        ("国际", "🌍"),
        ("宏观", "🌍"),
        ("每日复盘", "🌇"),
        ("盘中茶歇", "🍵"),
        ("市场信息", "📰"),
        ("市场观察", "🔎"),
        ("观察标的", "👀"),
        ("复盘辅助", "🧾"),
    )
    for keyword, icon in icon_map:
        if keyword in title:
            return icon
    return "📌"


def _format_market_message(
    title: str,
    *,
    report_time: str,
    source: str,
    category: str,
    importance: str,
    summary: str,
    impact: str = "见上方摘要",
    links: str = "未知",
    market_scope: str = "其他",
    related_sectors: Any = None,
    include_title: bool = True,
) -> str:
    """Build a stable Telegram information template."""
    title_prefix = f"{_title_icon(title)} {title}\n\n" if include_title else ""
    message = (
        f"{title_prefix}"
        f"【时间】{report_time or '未知'}\n"
        f"【来源】{source or '未知'}\n"
        f"【分类】{_display_category(category)}\n"
        f"【重要性】{_display_importance(importance)}\n"
        f"【影响范围】{market_scope or '其他'}\n"
        f"【相关板块】{_format_related_sectors(related_sectors)}\n"
        f"【摘要】{_soften_trading_language(summary)}\n"
        f"【可能影响】{_soften_trading_language(impact)}\n"
        f"【原文链接】{links or '未知'}"
    )
    return message


@_with_run_summary("recommend")
def run_recommend() -> None:
    from core.analyzers.recommend import run_recommend as _run_recommend

    _run_recommend()

@_with_run_summary("track")
def run_track() -> None:
    from core.analyzers.track import run_track as _run_track

    _run_track()

@_with_run_summary(lambda mode: mode)
def run_analysis(mode: str) -> None:
    reset_data_source_health()
    log_info(f"启动：通用分析模式 [{mode}]")
    prompts = load_prompts()

    if mode == "funds":
        from core.analyzers.funds import run_funds

        run_funds(prompts)
        return

    if mode == "daily":
        from core.analyzers.daily import run_daily

        run_daily(prompts)
        return

    if mode == "monitor":
        from core.analyzers.monitor import run_monitor

        run_monitor(prompts)
        return

    if mode == "global":
        from core.analyzers.global_macro import run_global

        run_global(prompts)
        return

    if mode == "periodic":
        from core.analyzers.periodic import run_periodic

        run_periodic(prompts)
        return

    if mode == "after_market":
        from core.analyzers.after_market import run_after_market

        run_after_market(prompts)
        return


@_with_run_summary("review")
def run_review() -> None:
    from core.analyzers.review import run_review as _run_review

    _run_review()
