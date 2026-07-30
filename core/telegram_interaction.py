"""Long-poll Telegram callback listener for the interactive market radar."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

from config import settings
from core.analyzers.monitor import (
    NEWS_TRACK_CALLBACK_PREFIX,
    handle_news_tracking_callback,
)
from core.radar import handle_radar_callback
from core.radar_store import RadarStore
from utils.notifier import log_error, log_info


TELEGRAM_API_ROOT = "https://api.telegram.org/bot{token}/{method}"


def _telegram_post(method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Call Telegram without ever logging a token or raw response body."""
    if not settings.INTERACTION_BOT_TOKEN:
        log_error("❌ Telegram 交互监听缺少 Bot Token")
        return None
    try:
        response = requests.post(
            TELEGRAM_API_ROOT.format(
                token=settings.INTERACTION_BOT_TOKEN, method=method
            ),
            json=payload,
            timeout=35,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        log_error(f"❌ Telegram 交互接口 {method} 失败: {exc.__class__.__name__}")
        return None
    if not body.get("ok"):
        log_error(f"❌ Telegram 交互接口 {method} 返回失败")
        return None
    result = body.get("result")
    return result if isinstance(result, dict) else {"items": result}


def _get_updates(offset: int | None) -> list[dict[str, Any]] | None:
    payload: dict[str, Any] = {
        "timeout": 25,
        "allowed_updates": ["callback_query"],
    }
    if offset is not None:
        payload["offset"] = offset
    result = _telegram_post("getUpdates", payload)
    if result is None:
        return None
    items = result.get("items")
    if not isinstance(items, list):
        log_error("❌ Telegram getUpdates 返回格式异常")
        return None
    return [item for item in items if isinstance(item, dict)]


def _answer_callback(callback: dict[str, Any], notice: str) -> None:
    callback_id = str(callback.get("id") or "")
    if not callback_id:
        return
    _telegram_post(
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": notice[:180], "show_alert": False},
    )


def _handle_callback(callback: dict[str, Any], now: datetime) -> str:
    """Route only known button namespaces to their dedicated handlers."""
    data = str(callback.get("data") or "")
    if data.startswith(f"{NEWS_TRACK_CALLBACK_PREFIX}:"):
        return handle_news_tracking_callback(callback, now)
    return handle_radar_callback(callback, now)


def run_telegram_listener() -> None:
    """Run one dedicated process; Telegram long-polling needs no public web port."""
    if not settings.INTERACTION_BOT_TOKEN or not settings.INTERACTION_CHAT_ID:
        raise RuntimeError("Telegram 交互监听缺少机器人或聊天配置")

    store = RadarStore(settings.MONITOR_DB_FILE)
    store.initialize()
    log_info("Telegram 雷达交互监听已启动")
    while True:
        last_update = store.last_telegram_update_id()
        updates = _get_updates(last_update + 1 if last_update is not None else None)
        if updates is None:
            time.sleep(5)
            continue
        for update in updates:
            update_id = update.get("update_id")
            try:
                callback = update.get("callback_query")
                if not isinstance(callback, dict):
                    continue
                notice = _handle_callback(callback, datetime.now(settings.SHA_TZ))
                _answer_callback(callback, notice)
                log_info(f"Telegram 雷达交互: {notice}")
            except Exception as exc:
                log_error(f"❌ Telegram 雷达交互处理失败: {exc.__class__.__name__}")
            finally:
                try:
                    store.set_last_telegram_update_id(int(update_id), datetime.now(settings.SHA_TZ))
                except (TypeError, ValueError):
                    log_error("❌ Telegram update_id 格式异常，未写入监听游标")
