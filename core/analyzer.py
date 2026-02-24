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

DEFAULT_FEATURE_PROMPT = (
    "你是金融新闻特征提取器。只输出严格 JSON，不要输出 Markdown，不要输出额外解释。\n"
    "输入新闻：\n{news_txt}\n\n"
    "返回 JSON 对象，且仅包含以下字段：\n"
    "sentiment_score: float，范围 0.0~1.0；\n"
    "duration_impact: float，范围 1~5（单位：天）；\n"
    "sector_relevance: float，范围 0.0~1.0。"
)


def load_prompts() -> dict[str, str]:
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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _extract_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    match = re.search(r"\{[\s\S]*\}", str(raw_text or ""))
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _parse_feature_json(raw_text: str) -> Optional[dict[str, float]]:
    parsed = _extract_json_object(raw_text)
    if not parsed:
        return None
    try:
        sentiment_score = _clamp(float(parsed["sentiment_score"]), 0.0, 1.0)
        duration_impact = _clamp(float(parsed["duration_impact"]), 1.0, 5.0)
        sector_relevance = _clamp(float(parsed["sector_relevance"]), 0.0, 1.0)
    except (KeyError, ValueError, TypeError):
        return None
    return {
        "sentiment_score": sentiment_score,
        "duration_impact": duration_impact,
        "sector_relevance": sector_relevance,
    }


def _extract_features_with_retry(news_text: str, prompts: dict[str, str], retries: int = 3) -> Optional[dict[str, float]]:
    template = prompts.get("feature_extract", DEFAULT_FEATURE_PROMPT)
    prompt = template.format(news_txt=news_text)
    for _ in range(retries):
        content = get_ai_response(prompt, temperature=0.0)
        if not content:
            continue
        parsed = _parse_feature_json(content)
        if parsed:
            return parsed
    log_error("⚠️ 特征提取失败：多次 JSON 解析失败")
    return None


def _append_history(pick_data: dict[str, Any], start_price: str) -> None:
    try:
        today_str = datetime.now(settings.SHA_TZ).strftime("%Y-%m-%d")
        file_exists = os.path.isfile(settings.HISTORY_FILE)
        with open(settings.HISTORY_FILE, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(["Date", "Name", "Code", "Start_Price", "Reason"])
            writer.writerow([today_str, pick_data["name"], pick_data["code"], start_price, pick_data["reason"]])
    except Exception as exc:
        log_error(f"❌ 历史写入失败: {exc}")


def _aggregate_news_features(news_items: list[dict[str, Any]], prompts: dict[str, str], limit: int = 20) -> Optional[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for item in news_items[:limit]:
        text = f"[{item.get('source','unknown')}] {item.get('title','')}\n{item.get('digest','')}"
        feat = _extract_features_with_retry(text, prompts)
        if feat:
            rows.append(feat)

    if not rows:
        return None

    count = float(len(rows))
    return {
        "sentiment_score": sum(x["sentiment_score"] for x in rows) / count,
        "duration_impact": sum(x["duration_impact"] for x in rows) / count,
        "sector_relevance": sum(x["sector_relevance"] for x in rows) / count,
    }


def _safe_pct_value(raw_pct: Any) -> tuple[Optional[float], str]:
    text = str(raw_pct).replace("%", "").strip()
    try:
        pct_num = float(text)
        return pct_num, f"{pct_num:.2f}"
    except (ValueError, TypeError):
        return None, text


def run_recommend() -> None:
    log_info("启动：规则选股（LLM仅提取特征）")
    candidates = get_hot_stocks_data()
    if not candidates:
        return

    prompts = load_prompts()
    news = get_news(720)
    if not news:
        return

    candidate_scores: list[tuple[float, dict[str, Any]]] = []
    for stock in candidates[:20]:
        matched_news = [n for n in news if str(stock.get("name", "")) and str(stock["name"]) in f"{n.get('title','')} {n.get('digest','')}"]
        if not matched_news:
            candidate_scores.append((0.0, stock))
            continue
        agg = _aggregate_news_features(matched_news, prompts, limit=8)
        if not agg:
            candidate_scores.append((0.0, stock))
            continue
        score = agg["sentiment_score"] * agg["duration_impact"] * agg["sector_relevance"]
        candidate_scores.append((score, stock))

    candidate_scores.sort(key=lambda x: x[0], reverse=True)
    pick_stock = candidate_scores[0][1]

    quote = get_stock_quote(pick_stock["code"])
    if not quote:
        return

    pick_data = {
        "name": pick_stock["name"],
        "code": pick_stock["code"],
        "reason": "基于新闻特征评分（sentiment_score × duration_impact × sector_relevance）最高",
    }

    try:
        with open(settings.PICK_FILE, "w", encoding="utf-8") as file:
            json.dump(pick_data, file, ensure_ascii=False, indent=2)
    except Exception as exc:
        log_error(f"❌ 选股结果写入失败: {exc}")
        return

    _append_history(pick_data, quote["price"])
    send_tg(
        f"<b>🎯 今日规则精选</b>\n\n🦄 <b>{pick_data['name']} ({pick_data['code']})</b>\n当前价: {quote['price']}\n\n"
        f"🧮 依据: {pick_data['reason']}"
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

    quote = get_stock_quote(pick_data.get("code"))
    if not quote:
        return

    pct_num, pct_for_msg = _safe_pct_value(quote.get("pct", "-"))
    if pct_num is None:
        decision = "数据不足，继续观察"
    elif pct_num >= 5:
        decision = "涨幅较大，分批止盈"
    elif pct_num <= -3:
        decision = "跌破风控阈值，考虑止损"
    else:
        decision = "波动正常，持仓观察"

    icon = "🔴" if pct_num is not None and pct_num > 0 else "🟢"
    send_tg(
        f"<b>👀 选股跟踪: {pick_data.get('name','-')}</b>\n\n{icon} 现价: {quote['price']} ({pct_for_msg}%)\n\n"
        f"📌 规则建议：{decision}"
    )


def run_analysis(mode: str) -> None:
    log_info(f"启动：通用分析模式 [{mode}]")
    prompts = load_prompts()

    if mode == "funds":
        top_in, top_out = get_market_funds()
        if not top_in:
            return
        send_tg(
            "<b>💰 主力资金雷达</b>\n\n"
            + "主力流入TOP:\n"
            + "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in[:5]])
            + "\n\n主力流出TOP:\n"
            + "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out[:5]])
        )
        return

    lookback_map = {"daily": 1440, "monitor": 90, "global": 180, "periodic": 240, "after_market": 240}
    if mode not in lookback_map:
        return

    news = get_news(lookback_map[mode])
    if not news:
        return

    if mode == "monitor":
        now = datetime.now(settings.SHA_TZ)
        strict_threshold = now - timedelta(minutes=15)
        soft_threshold = now - timedelta(minutes=30)
        filtered = []
        for item in news:
            if item["datetime"] >= strict_threshold:
                filtered.append(item)
            elif item["datetime"] >= soft_threshold and any(k in f"{item['title']} {item['digest']}" for k in HIGH_IMPACT_KEYWORDS):
                filtered.append(item)
        news = filtered
        if not news:
            return

    agg = _aggregate_news_features(news, prompts)
    if not agg:
        return

    title_map = {
        "daily": "🌅 股市特征概览",
        "monitor": "🎯 机会雷达（特征版）",
        "global": "🌐 国际宏观特征雷达",
        "periodic": "🍵 盘中特征简报",
        "after_market": "🌇 收盘特征复盘",
    }

    lines = [f"- [{n.get('source','unknown')}] {n.get('title','')}" for n in news[:5]]
    send_tg(
        f"<b>{title_map[mode]}</b>\n\n"
        f"sentiment_score: <b>{agg['sentiment_score']:.3f}</b>\n"
        f"duration_impact: <b>{agg['duration_impact']:.2f} 天</b>\n"
        f"sector_relevance: <b>{agg['sector_relevance']:.3f}</b>\n\n"
        "样本新闻:\n" + "\n".join(lines)
    )


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
            details.append(f"{icon} <b>{row['Name']}</b>: <b>{pct:+.2f}%</b>")

        if total_count == 0:
            return
        win_rate = (win_count / total_count) * 100
        avg_profit = total_profit / total_count
        send_tg(
            f"<b>📊 AI 战绩周报</b>\n\n🏆 <b>胜率: {win_rate:.0f}%</b>\n💰 <b>平均收益: {avg_profit:+.2f}%</b>\n------------------\n"
            + "\n".join(details)
        )
    except Exception as exc:
        log_error(f"复盘失败: {exc}")
