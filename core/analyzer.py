from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from config import settings
from core.data_fetcher import get_hot_stocks_data, get_market_funds, get_news, get_stock_quote
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


def run_recommend() -> None:
    log_info("启动：AI 选股推荐")
    candidates = get_hot_stocks_data()
    if not candidates:
        return

    candidates_str = "\n".join(
        [f"- {s['name']} (代码:{s['code']}, 涨幅:{s['pct']}, 成交:{s['amount']})" for s in candidates]
    )
    news = get_news(720)
    news_txt = "\n".join([f"- {n['title']}" for n in news[:15]])
    base_prompt = (
        "你是极其理性的量化交易员。请从下方的【候选股票列表】中，挑选唯一一只最符合当前市场热点和新闻面的股票。\n\n"
        f"【候选股票列表】:\n{candidates_str}\n\n【近期新闻】:\n{news_txt}\n\n"
        "要求：\n1. 必须从候选列表中选一只，绝对禁止捏造。\n2. 输出 JSON 格式：{\"name\": \"股票名\", \"code\": \"6位代码\", \"reason\": \"简短理由\"}"
    )

    content = get_ai_response(base_prompt, temperature=0.1)
    if not content:
        return

    pick_data = _extract_pick_data(content)
    if not pick_data:
        return

    quote = get_stock_quote(pick_data["code"])
    if not quote:
        return

    try:
        with open(settings.PICK_FILE, "w", encoding="utf-8") as file:
            json.dump(pick_data, file, ensure_ascii=False, indent=2)
    except Exception as exc:
        log_error(f"❌ 选股结果写入失败: {exc}")
        return

    _append_history(pick_data, quote["price"])
    send_tg(
        f"🎯 今日AI精选 (Pro版)\n\n🦄 {pick_data['name']} ({pick_data['code']})\n当前价: {quote['price']}\n\n📝 逻辑：\n{pick_data['reason']}"
    )


def run_track() -> None:
    if not os.path.exists(settings.PICK_FILE):
        return

    try:
        with open(settings.PICK_FILE, "r", encoding="utf-8") as file:
            pick_data = json.load(file)
    except Exception as exc:
        log_error(f"❌ 读取选股文件失败: {exc}")
        return

    try:
        quote = get_stock_quote(pick_data["code"])
        if not quote:
            return

        pct_num, pct_for_prompt = _safe_pct_value(quote.get("pct", "-"))
        prompts = load_prompts()
        track_prompt = prompts.get("track", settings.DEFAULT_PROMPTS["track"]).format(
            name=pick_data["name"],
            code=pick_data["code"],
            price=quote["price"],
            pct=pct_for_prompt,
        )
        analysis = get_ai_response(track_prompt)
        if not analysis:
            return

        icon = "🔴" if pct_num is not None and pct_num > 0 else "🟢"
        send_tg(
            f"👀 选股跟踪: {pick_data['name']}\n\n{icon} 现价: {quote['price']} ({pct_for_prompt}%)\n\n🧠 AI观点：\n{analysis}"
        )
    except Exception as exc:
        log_error(f"❌ 追踪失败: {exc}")


def run_analysis(mode: str) -> None:
    log_info(f"启动：通用分析模式 [{mode}]")
    prompts = load_prompts()

    if mode == "funds":
        now = datetime.now(settings.SHA_TZ)
        top_in, top_out = get_market_funds()
        if not top_in:
            return
        in_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out])
        news = get_news(720)
        news_txt = "\n".join(
            [f"- [{n.get('source', 'unknown')}] {n.get('time_str', '')} {n['title']}" for n in news[:20]]
        )
        content = get_ai_response(
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
            send_tg(f"💰 主力资金雷达 ({now.strftime('%Y-%m-%d')})\n\n{content}")
        return

    if mode == "daily":
        now = datetime.now(settings.SHA_TZ)
        news = get_news(1440)
        if not news:
            return
        news_txt = "\n".join(
            [f"- [{n.get('source', 'unknown')}] {n.get('time_str', '')} {n['title']}" for n in news[:30]]
        )
        content = get_ai_response(
            prompts.get("daily", settings.DEFAULT_PROMPTS["daily"]).format(
                news_txt=news_txt,
                report_date=now.strftime("%Y-%m-%d"),
                report_time=now.strftime("%Y-%m-%d %H:%M"),
            ),
            model="deepseek-reasoner",
        )
        if content:
            send_tg(f"🌅 股市全景内参 ({now.strftime('%Y-%m-%d')})\n\n{content}")
        return

    if mode == "monitor":
        news = get_news(90)
        now = datetime.now(settings.SHA_TZ)
        strict_threshold = now - timedelta(minutes=15)
        soft_threshold = now - timedelta(minutes=30)

        fresh_news: list[dict[str, Any]] = []
        for item in news:
            if item["datetime"] >= strict_threshold:
                fresh_news.append(item)
            elif item["datetime"] >= soft_threshold and any(
                keyword in f"{item['title']} {item['digest']}" for keyword in HIGH_IMPACT_KEYWORDS
            ):
                fresh_news.append(item)

        if not fresh_news:
            return

        dedup_news: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for item in fresh_news:
            if item["title"] not in seen_titles:
                seen_titles.add(item["title"])
                dedup_news.append(item)

        news_titles = [f"{i}. [{n.get('source', 'unknown')}] {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(dedup_news[:12])]
        content = get_ai_response(prompts.get("monitor", settings.DEFAULT_PROMPTS["monitor"]).format(news_list="\n".join(news_titles)))
        if not content:
            return

        alerts_buffer: list[str] = []
        alert_links: list[str] = []
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
                alerts_buffer.append(
                    f"💡 逻辑：{parts[2]}\n📰 {item['title']} ({item['time_str']})"
                )
                alert_links.append(str(item["link"]))

        if alerts_buffer:
            msg = "🎯 机会雷达汇总\n\n" + "\n\n〰️〰️〰️〰️〰️\n\n".join(alerts_buffer[:3])
            if alert_links[:3]:
                msg += "\n\n链接：\n" + "\n".join(alert_links[:3])
            send_tg(msg, token=settings.TG_BOT_TOKEN_MONITOR, chat_id=settings.TG_CHAT_ID_MONITOR)
        return

    if mode == "global":
        news = get_news(180)
        if not news:
            return
        news_txt = "\n".join([f"- [{n.get('source', 'unknown')}] {n['title']} (详情:{n['digest'][:40]})" for n in news[:80]])
        content = get_ai_response(prompts.get("global", settings.DEFAULT_PROMPTS["global"]).format(news_txt=news_txt))
        if content and "无重大事件" not in content:
            send_tg(
                f"🌐 国际宏观与板块雷达 (3H)\n\n{content}",
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
            return

        if mode == "after_market":
            news_txt = "\n".join(
                [f"- [{n.get('source', 'unknown')}] {n.get('time_str', '')} {n['title']}" for n in news[:25]]
            )
        else:
            news_txt = "\n".join([f"- [{n.get('source', 'unknown')}] {n['title']}" for n in news[:25]])

        title = "🌇 每日复盘" if mode == "after_market" else "🍵 盘中茶歇"
        content = get_ai_response(
            prompts.get(mode, settings.DEFAULT_PROMPTS[mode]).format(
                news_txt=news_txt,
                report_date=now.strftime("%Y-%m-%d"),
                report_time=now.strftime("%Y-%m-%d %H:%M"),
            )
        )
        if content:
            message_title = f"{title} ({now.strftime('%Y-%m-%d')})" if mode == "after_market" else title
            send_tg(f"{message_title}\n\n{content}")


def run_review() -> None:
    if not os.path.exists(settings.HISTORY_FILE):
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
            return
        win_rate = (win_count / total_count) * 100
        avg_profit = total_profit / total_count
        send_tg(
            f"📊 AI 战绩周报\n\n🏆 胜率: {win_rate:.0f}%\n💰 平均收益: {avg_profit:+.2f}%\n------------------\n"
            + "\n".join(details)
        )
    except Exception as exc:
        log_error(f"复盘失败: {exc}")
