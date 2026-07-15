from __future__ import annotations

import json
import os
from typing import Any, Optional

from config import settings
from core.data_fetcher import record_data_source_health, reset_data_source_health
from core.runtime import (
    _set_run_summary,
    _with_run_summary,
)
from utils.ai_client import get_ai_response
from utils.notifier import log_error, log_info

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


def _extract_pick_data(content: str) -> Optional[dict[str, Any]]:
    text = str(content or "").strip()
    start_idx = text.find("{")
    if start_idx == -1:
        log_error("❌ AI 返回内容中未找到 JSON")
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text[start_idx:])
    except json.JSONDecodeError as exc:
        log_error(f"❌ AI 返回 JSON 解析失败: {exc}")
        return None
    required_keys = ("name", "code", "reason")
    if not isinstance(parsed, dict) or any(key not in parsed for key in required_keys):
        log_error("❌ AI 返回 JSON 缺少必要字段(name/code/reason)")
        return None
    return parsed


def _normalize_stock_code(raw_code: Any) -> str:
    code = str(raw_code or "").strip()
    if not code.isdigit() or len(code) > 6:
        return ""
    return code.zfill(6)


def _validate_pick_in_candidates(
    pick_data: dict[str, Any], candidates: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Return normalized pick data only when the AI chose a provided candidate."""
    pick_code = _normalize_stock_code(pick_data.get("code"))
    candidates_by_code = {
        _normalize_stock_code(candidate.get("code")): candidate
        for candidate in candidates
        if _normalize_stock_code(candidate.get("code"))
    }
    candidate = candidates_by_code.get(pick_code)
    if not candidate:
        log_error(f"❌ AI 返回候选列表外股票代码: {pick_code or '空'}")
        return None

    normalized = dict(pick_data)
    normalized["code"] = pick_code
    normalized["name"] = str(candidate.get("name") or pick_data.get("name") or "未知")
    return normalized


def _safe_pct_value(raw_pct: Any) -> tuple[Optional[float], str]:
    text = str(raw_pct).replace("%", "").strip()
    try:
        pct_num = float(text)
        return pct_num, f"{pct_num:.2f}"
    except (ValueError, TypeError):
        return None, text


def _get_ai_response_with_health(*args, **kwargs) -> Optional[str]:
    _set_run_summary(ai_called=True)
    content = get_ai_response(*args, **kwargs)
    if str(content or "").strip():
        record_data_source_health("DeepSeek", "success", "", 1)
        return content
    record_data_source_health("DeepSeek", "empty", "返回空内容", 0)
    return None


def _has_effective_content(content: Any) -> bool:
    return bool(str(content or "").strip())


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
