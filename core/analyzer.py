from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from config import settings
from core.data_fetcher import (
    get_data_source_health,
    get_hot_stocks_data,
    get_market_funds,
    get_news,
    get_stock_quote,
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
    log_info(_format_health_status_message(reason))


def _get_ai_response_with_health(*args, **kwargs) -> Optional[str]:
    """Call DeepSeek through the existing client and record concise health state."""
    content = get_ai_response(*args, **kwargs)
    if str(content or "").strip():
        record_data_source_health("DeepSeek", "success", "", 1)
        return content
    record_data_source_health("DeepSeek", "empty", "返回空内容", 0)
    return None


def _has_effective_content(content: Any) -> bool:
    """Return whether a generated Telegram body has visible text."""
    return bool(str(content or "").strip())


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
    title_prefix = f"📌 {title}\n\n" if include_title else ""
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


def run_recommend() -> None:
    reset_data_source_health()
    log_info("启动：AI 选股推荐")
    candidates = get_hot_stocks_data()
    if not candidates:
        _send_health_status("热门股数据为空，无法生成观察记录")
        return

    candidates_str = "\n".join(
        [
            f"- {s['name']} (代码:{s['code']}, 涨幅:{s['pct']}, 成交:{s['amount']})"
            for s in candidates
        ]
    )
    news = get_news(720)
    news_txt = "\n".join(
        [_format_news_prompt_line(n, include_time=False) for n in news[:15]]
    )
    base_prompt = (
        "你是极其理性的量化交易员。请从下方的【候选股票列表】中，挑选唯一一只最符合当前市场热点和新闻面的股票。\n\n"
        f"【候选股票列表】:\n{candidates_str}\n\n【近期新闻】:\n{news_txt}\n\n"
        '要求：\n1. 必须从候选列表中选一只，绝对禁止捏造。\n2. 输出 JSON 格式：{"name": "股票名", "code": "6位代码", "reason": "简短理由"}'
    )

    content = _get_ai_response_with_health(base_prompt, temperature=0.1)
    if not content:
        _send_health_status("DeepSeek 没有生成有效摘要")
        return

    pick_data = _extract_pick_data(content)
    if not pick_data:
        _send_health_status("DeepSeek 返回内容无法解析为观察记录")
        return

    quote = get_stock_quote(pick_data["code"])
    if not quote:
        _send_health_status("个股行情为空，无法生成观察记录")
        return

    try:
        with open(settings.PICK_FILE, "w", encoding="utf-8") as file:
            json.dump(pick_data, file, ensure_ascii=False, indent=2)
    except Exception as exc:
        log_error(f"❌ 选股结果写入失败: {exc}")
        return

    _append_history(pick_data, quote["price"])
    now = datetime.now(settings.SHA_TZ)
    message = _format_market_message(
        "市场观察记录",
        report_time=now.strftime("%Y-%m-%d %H:%M"),
        source="热门股 / 近期新闻 / DeepSeek",
        category="观察记录",
        importance="低（观察记录）",
        summary=f"{pick_data['name']} ({pick_data['code']}) 被记录为观察标的，当前价 {quote['price']}。",
        impact=f"观察理由：{pick_data['reason']}",
        links="未知",
    )
    if _has_effective_content(message):
        send_tg(message)
    else:
        _send_health_status("最终 Telegram 正文为空")


def run_track() -> None:
    reset_data_source_health()
    if not os.path.exists(settings.PICK_FILE):
        _send_health_status("未找到观察标的记录")
        return

    try:
        with open(settings.PICK_FILE, "r", encoding="utf-8") as file:
            pick_data = json.load(file)
    except Exception as exc:
        log_error(f"❌ 读取选股文件失败: {exc}")
        _send_health_status("观察标的记录读取失败")
        return

    try:
        quote = get_stock_quote(pick_data["code"])
        if not quote:
            _send_health_status("个股行情为空，无法跟踪观察标的")
            return

        pct_num, pct_for_prompt = _safe_pct_value(quote.get("pct", "-"))
        prompts = load_prompts()
        track_prompt = prompts.get("track", settings.DEFAULT_PROMPTS["track"]).format(
            name=pick_data["name"],
            code=pick_data["code"],
            price=quote["price"],
            pct=pct_for_prompt,
        )
        analysis = _get_ai_response_with_health(track_prompt)
        if not analysis:
            _send_health_status("DeepSeek 没有生成有效摘要")
            return

        icon = "🔴" if pct_num is not None and pct_num > 0 else "🟢"
        now = datetime.now(settings.SHA_TZ)
        message = _format_market_message(
            "观察标的跟踪",
            report_time=now.strftime("%Y-%m-%d %H:%M"),
            source="stock_pick.json / 东方财富行情 / DeepSeek",
            category="观察记录",
            importance="低（观察记录）",
            summary=f"{icon} {pick_data['name']} ({pick_data['code']}) 当前价 {quote['price']}，涨跌幅 {pct_for_prompt}%。",
            impact=f"观察观点：{analysis}",
            links="未知",
        )
        if _has_effective_content(message):
            send_tg(message)
        else:
            _send_health_status("最终 Telegram 正文为空")
    except Exception as exc:
        log_error(f"❌ 追踪失败: {exc}")
        _send_health_status("观察标的跟踪发生异常")


def run_analysis(mode: str) -> None:
    reset_data_source_health()
    log_info(f"启动：通用分析模式 [{mode}]")
    prompts = load_prompts()

    if mode == "funds":
        now = datetime.now(settings.SHA_TZ)
        top_in, top_out = get_market_funds()
        if not top_in:
            _send_health_status("资金流数据为空，无法生成资金流摘要")
            return
        in_str = "\n".join(
            [f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in]
        )
        out_str = "\n".join(
            [f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out]
        )
        news = get_news(720)
        news_txt = "\n".join(
            [_format_news_prompt_line(n, include_time=True) for n in news[:20]]
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
            send_tg(
                _format_market_message(
                    "主力资金雷达",
                    report_time=now.strftime("%Y-%m-%d %H:%M"),
                    source=_format_sources(news, "东方财富资金流 / 新闻源"),
                    category="capital_flow",
                    importance="medium",
                    summary=content,
                    impact="结合行业资金流、板块涨跌和近期消息，仅作市场观察参考。",
                    links=_format_links([item.get("link") for item in news[:5]]),
                    market_scope="行业",
                    related_sectors=[s["name"] for s in top_in[:3]],
                )
            )
        else:
            _send_health_status("DeepSeek 没有生成有效摘要")
        return

    if mode == "daily":
        now = datetime.now(settings.SHA_TZ)
        news = get_news(1440)
        if not news:
            _send_health_status("新闻数据为空，无法生成每日摘要")
            return
        news_txt = "\n".join(
            [_format_news_prompt_line(n, include_time=True) for n in news[:30]]
        )
        content = _get_ai_response_with_health(
            prompts.get("daily", settings.DEFAULT_PROMPTS["daily"]).format(
                news_txt=news_txt,
                report_date=now.strftime("%Y-%m-%d"),
                report_time=now.strftime("%Y-%m-%d %H:%M"),
            ),
            model="deepseek-reasoner",
        )
        if content:
            send_tg(
                _format_market_message(
                    "今日市场信息摘要",
                    report_time=now.strftime("%Y-%m-%d %H:%M"),
                    source=_format_sources(news, "东方财富 / RSS"),
                    category="other",
                    importance="medium",
                    summary=content,
                    impact="用于快速了解市场主线、情绪和风险偏好，不构成买卖依据。",
                    links=_format_links([item.get("link") for item in news[:5]]),
                    market_scope="A股",
                    related_sectors=[
                        sector
                        for item in news[:20]
                        for sector in item.get("related_sectors", [])
                    ][:6],
                )
            )
        else:
            _send_health_status("DeepSeek 没有生成有效摘要")
        return

    if mode == "monitor":
        news = get_news(90)
        if not news:
            _send_health_status(
                "新闻数据为空，无法生成监控摘要",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
            return
        now = datetime.now(settings.SHA_TZ)
        strict_threshold = now - timedelta(minutes=15)
        soft_threshold = now - timedelta(minutes=30)

        fresh_news: list[dict[str, Any]] = []
        for item in news:
            if item["datetime"] >= strict_threshold:
                fresh_news.append(item)
            elif item["datetime"] >= soft_threshold and any(
                keyword in f"{item['title']} {item['digest']}"
                for keyword in HIGH_IMPACT_KEYWORDS
            ):
                fresh_news.append(item)

        if not fresh_news:
            _send_health_status(
                "未发现符合时间窗口的重要市场信息",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
            return

        dedup_news: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for item in fresh_news:
            if item["title"] not in seen_titles:
                seen_titles.add(item["title"])
                dedup_news.append(item)

        news_titles = [
            (
                f"{i}. [{n.get('source', 'unknown')}] "
                f"[分类:{_display_category(n.get('category'))} / "
                f"重要性:{_display_importance(n.get('importance'))} / "
                f"范围:{n.get('market_scope') or '其他'}] "
                f"{n['title']} (详情:{n['digest'][:60]})"
            )
            for i, n in enumerate(dedup_news[:12])
        ]
        content = _get_ai_response_with_health(
            prompts.get("monitor", settings.DEFAULT_PROMPTS["monitor"]).format(
                news_list="\n".join(news_titles)
            )
        )
        if not content:
            _send_health_status(
                "DeepSeek 没有生成有效摘要",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
            return

        alerts_buffer: list[str] = []
        for line in content.split("\n"):
            if "ALERT|" not in line:
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            try:
                idx = int(re.sub(r"\D", "", parts[1]))
            except ValueError:
                continue
            if idx < len(dedup_news):
                item = dedup_news[idx]
                link = str(item.get("link") or "").strip()
                alerts_buffer.append(
                    _format_market_message(
                        "市场信息摘要",
                        report_time=_format_news_time(item),
                        source=str(item.get("source") or "未知"),
                        category=_infer_news_category(item),
                        importance=_infer_market_importance(item),
                        summary=str(item.get("title") or "未知"),
                        impact=parts[2],
                        links=link or "未知",
                        market_scope=str(item.get("market_scope") or "其他"),
                        related_sectors=item.get("related_sectors"),
                        include_title=False,
                    )
                )

        if alerts_buffer:
            msg = "📌 市场信息摘要\n\n" + "\n\n〰️〰️〰️\n\n".join(alerts_buffer[:3])
            if _has_effective_content(msg):
                send_tg(
                    msg,
                    token=settings.TG_BOT_TOKEN_MONITOR,
                    chat_id=settings.TG_CHAT_ID_MONITOR,
                )
            else:
                _send_health_status(
                    "最终 Telegram 正文为空",
                    token=settings.TG_BOT_TOKEN_MONITOR,
                    chat_id=settings.TG_CHAT_ID_MONITOR,
                )
        else:
            _send_health_status(
                "DeepSeek 未识别需提醒的市场信息",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
        return

    if mode == "global":
        news = get_news(180)
        if not news:
            _send_health_status(
                "海外新闻数据为空，无法生成全球摘要",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
            return
        news_txt = "\n".join(
            [
                f"{_format_news_prompt_line(n, include_time=False)} (详情:{n['digest'][:40]})"
                for n in news[:80]
            ]
        )
        content = _get_ai_response_with_health(
            prompts.get("global", settings.DEFAULT_PROMPTS["global"]).format(
                news_txt=news_txt
            )
        )
        if content and "无重大事件" not in content:
            now = datetime.now(settings.SHA_TZ)
            send_tg(
                _format_market_message(
                    "国际宏观与板块雷达",
                    report_time=now.strftime("%Y-%m-%d %H:%M"),
                    source=_format_sources(news, "Reuters / RSS"),
                    category="overseas",
                    importance="medium",
                    summary=content,
                    impact="用于观察海外事件对全球市场、A股映射板块和风险偏好的可能影响。",
                    links=_format_links([item.get("link") for item in news[:5]]),
                    market_scope="全球",
                    related_sectors=[
                        sector
                        for item in news[:20]
                        for sector in item.get("related_sectors", [])
                    ][:6],
                ),
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
        else:
            _send_health_status(
                "DeepSeek 没有生成有效摘要或判断无重大事件",
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
        return

    if mode in ["periodic", "after_market"]:
        now = datetime.now(settings.SHA_TZ)
        if mode == "after_market" and now.weekday() >= 5:
            log_info("周末跳过：每日复盘不发送")
            return

        news = get_news(240)
        if not news:
            _send_health_status("新闻数据为空，无法生成市场简报")
            return

        if mode == "after_market":
            news_txt = "\n".join(
                [_format_news_prompt_line(n, include_time=True) for n in news[:25]]
            )
        else:
            news_txt = "\n".join(
                [_format_news_prompt_line(n, include_time=False) for n in news[:25]]
            )

        title = "🌇 每日复盘" if mode == "after_market" else "🍵 盘中茶歇"
        content = _get_ai_response_with_health(
            prompts.get(mode, settings.DEFAULT_PROMPTS[mode]).format(
                news_txt=news_txt,
                report_date=now.strftime("%Y-%m-%d"),
                report_time=now.strftime("%Y-%m-%d %H:%M"),
            )
        )
        if content:
            category = "复盘" if mode == "after_market" else "盘中"
            importance = (
                "中（市场复盘）" if mode == "after_market" else "低（盘中简报）"
            )
            impact = (
                "用于回看当日市场结构、资金偏好和次日风险点。"
                if mode == "after_market"
                else "用于盘中快速过滤新闻噪音和观察市场情绪。"
            )
            send_tg(
                _format_market_message(
                    title,
                    report_time=now.strftime("%Y-%m-%d %H:%M"),
                    source=_format_sources(news, "东方财富 / RSS"),
                    category=category,
                    importance=importance,
                    summary=content,
                    impact=impact,
                    links=_format_links([item.get("link") for item in news[:5]]),
                )
            )
        else:
            _send_health_status("DeepSeek 没有生成有效摘要")


def run_review() -> None:
    reset_data_source_health()
    if not os.path.exists(settings.HISTORY_FILE):
        _send_health_status("未找到历史观察记录")
        return

    try:
        with open(settings.HISTORY_FILE, "r", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

        recent_rows = rows[-10:] if len(rows) > 10 else rows
        details: list[str] = []
        total_count = 0
        win_count = 0
        total_profit = 0.0

        for row in recent_rows:
            curr_quote = get_stock_quote(row["Code"])
            if not curr_quote:
                continue
            try:
                start = float(row["Start_Price"])
                curr = float(curr_quote["price"])
                pct = (curr - start) / start * 100
            except (ValueError, TypeError, ZeroDivisionError):
                continue

            total_count += 1
            total_profit += pct
            if pct > 0:
                win_count += 1
            icon = "🔴" if pct > 0 else "🟢"
            details.append(f"{icon} {row['Name']}: {pct:+.2f}%")

        if total_count == 0:
            _send_health_status("历史观察记录没有可用行情数据")
            return
        win_rate = (win_count / total_count) * 100
        avg_profit = total_profit / total_count
        now = datetime.now(settings.SHA_TZ)
        summary = (
            f"观察样本正收益占比: {win_rate:.0f}%\n"
            f"观察样本平均变化: {avg_profit:+.2f}%\n" + "\n".join(details)
        )
        send_tg(
            _format_market_message(
                "观察记录复盘辅助",
                report_time=now.strftime("%Y-%m-%d %H:%M"),
                source="history.csv / 东方财富行情",
                category="复盘辅助",
                importance="低（复盘辅助）",
                summary=summary,
                impact="仅用于回看观察记录表现，不能证明策略有效，也不构成后续操作建议。",
                links="未知",
            )
        )
    except Exception as exc:
        log_error(f"复盘失败: {exc}")
        _send_health_status("观察记录复盘发生异常")
