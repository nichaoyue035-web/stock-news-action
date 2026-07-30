"""Long-poll Telegram listener for interactive market alerts and radar candidates."""

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


def _telegram_post(
    method: str, payload: dict[str, Any], *, token: str
) -> dict[str, Any] | None:
    """Call Telegram without ever logging a token or raw response body."""
    if not token:
        log_error("❌ Telegram 交互监听缺少 Bot Token")
        return None
    try:
        response = requests.post(
            TELEGRAM_API_ROOT.format(token=token, method=method),
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


def _get_updates(offset: int | None, *, token: str) -> list[dict[str, Any]] | None:
    payload: dict[str, Any] = {
        "timeout": 25,
        "allowed_updates": ["callback_query", "message"],
    }
    if offset is not None:
        payload["offset"] = offset
    result = _telegram_post("getUpdates", payload, token=token)
    if result is None:
        return None
    items = result.get("items")
    if not isinstance(items, list):
        log_error("❌ Telegram getUpdates 返回格式异常")
        return None
    return [item for item in items if isinstance(item, dict)]


def _answer_callback(callback: dict[str, Any], notice: str, *, token: str) -> None:
    callback_id = str(callback.get("id") or "")
    if not callback_id:
        return
    _telegram_post(
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": notice[:180], "show_alert": False},
        token=token,
    )


def _handle_callback(callback: dict[str, Any], now: datetime) -> str:
    """Route only known button namespaces to their dedicated handlers."""
    data = str(callback.get("data") or "")
    if data.startswith(f"{NEWS_TRACK_CALLBACK_PREFIX}:"):
        return handle_news_tracking_callback(callback, now)
    return handle_radar_callback(callback, now)


def _handle_private_id_command(
    message: dict[str, Any], *, token: str | None = None
) -> bool:
    """Reply to a private /id request without authorizing the sender automatically."""
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    text = str(message.get("text") or "").strip().lower()
    if text.split("@", 1)[0] not in {"/id", "/start"}:
        return False
    chat_id = str(chat.get("id") or "")
    user_id = str(sender.get("id") or "")
    if chat.get("type") != "private" or not user_id or chat_id != user_id:
        return False
    _telegram_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": (
                f"你的 Telegram ID：{user_id}\n"
                "把它发给管理员，即可开通群组里的追踪按钮。"
            ),
        },
        token=token or settings.RADAR_INTERACTION_BOT_TOKEN,
    )
    return True


def _listener_targets() -> list[tuple[str, str]]:
    """Return the distinct bots that have callback buttons to process."""
    targets: list[tuple[str, str]] = []
    if settings.RADAR_INTERACTION_BOT_TOKEN and settings.RADAR_INTERACTION_CHAT_ID:
        targets.append(("radar_last_update_id", settings.RADAR_INTERACTION_BOT_TOKEN))
    if (
        settings.MARKET_ALERT_INTERACTION_ENABLED
        and settings.MARKET_INTERACTION_BOT_TOKEN
        and settings.MARKET_INTERACTION_CHAT_ID
        and all(token != settings.MARKET_INTERACTION_BOT_TOKEN for _, token in targets)
    ):
        targets.append(
            ("market_last_update_id", settings.MARKET_INTERACTION_BOT_TOKEN)
        )
    return targets


def run_telegram_listener() -> None:
    """Run one dedicated process; Telegram long-polling needs no public web port."""
    targets = _listener_targets()
    if not targets:
        raise RuntimeError("Telegram 交互监听缺少机器人或聊天配置")

    store = RadarStore(settings.MONITOR_DB_FILE)
    store.initialize()
    log_info("Telegram 标的与事件交互监听已启动")
    while True:
        polling_failed = False
        for state_key, token in targets:
            last_update = store.last_telegram_update_id(state_key)
            updates = _get_updates(
                last_update + 1 if last_update is not None else None, token=token
            )
            if updates is None:
                polling_failed = True
                continue
            for update in updates:
                update_id = update.get("update_id")
                try:
                    message = update.get("message")
                    if isinstance(message, dict):
                        _handle_private_id_command(message, token=token)
                        continue
                    callback = update.get("callback_query")
                    if not isinstance(callback, dict):
                        continue
                    notice = _handle_callback(callback, datetime.now(settings.SHA_TZ))
                    _answer_callback(callback, notice, token=token)
                    log_info(f"Telegram 交互: {notice}")
                except Exception as exc:
                    log_error(f"❌ Telegram 交互处理失败: {exc.__class__.__name__}")
                finally:
                    try:
                        store.set_last_telegram_update_id(
                            int(update_id),
                            datetime.now(settings.SHA_TZ),
                            state_key,
                        )
                    except (TypeError, ValueError):
                        log_error("❌ Telegram update_id 格式异常，未写入监听游标")
        if polling_failed:
            time.sleep(5)
