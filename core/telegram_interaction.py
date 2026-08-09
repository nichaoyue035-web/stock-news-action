"""Long-poll Telegram listener for interactive market alerts and radar candidates."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from config import settings
from core.analyzers.monitor import (
    NEWS_TRACK_CALLBACK_PREFIX,
    handle_news_tracking_callback,
)
from core.radar import handle_radar_callback
from core.radar_store import RadarStore
from core.runtime import get_run_status_file, write_service_heartbeat
from core.source_health import source_criticality
from utils.safety import redact_sensitive_text
from utils.notifier import (
    STATUS_CALLBACK_DATA,
    log_error,
    log_info,
    status_button_markup,
)


TELEGRAM_API_ROOT = "https://api.telegram.org/bot{token}/{method}"


def _status_button_markup() -> dict[str, Any]:
    """Keep the status panel and normal Telegram messages visually consistent."""
    return status_button_markup()


def _health_max_age_seconds(mode: str) -> int:
    try:
        minutes = int(os.getenv("HEALTH_MAX_AGE_MINUTES", "30"))
    except ValueError:
        minutes = 30
    if mode == "telegram_listener":
        return min(max(1, minutes), 3) * 60
    return max(1, minutes) * 60


def _format_status_message(now: datetime | None = None) -> str:
    """Format secret-free per-mode heartbeats for the monitoring chat."""
    now = now or datetime.now(settings.SHA_TZ)
    configured_modes = settings.HEALTH_REQUIRED_MODES or ("daily", "monitor")
    modes = tuple(
        dict.fromkeys((*configured_modes, "telegram_listener"))
    )
    lines = [f"📊 监控状态 · {now.strftime('%Y-%m-%d %H:%M')}"]
    all_healthy = True
    optional_degraded = False

    for mode in modes:
        try:
            status_path = Path(get_run_status_file(mode))
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if not isinstance(status, dict):
                raise ValueError("运行状态不是对象")
            finished_at = datetime.fromisoformat(str(status["finished_at"]))
            if finished_at.tzinfo is None:
                finished_at = finished_at.replace(tzinfo=settings.SHA_TZ)
            age_seconds = max(0, (now - finished_at).total_seconds())
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            lines.append(f"🔴 {mode}：无法读取状态（{exc.__class__.__name__}）")
            all_healthy = False
            continue

        status_value = str(status.get("status") or "未知")
        if age_seconds > _health_max_age_seconds(mode):
            icon, label = "🟠", "状态过期"
            all_healthy = False
        elif status_value == "success":
            icon, label = "🟢", "正常"
        elif status_value == "partial":
            icon, label = "🟡", "部分完成"
            all_healthy = False
        else:
            icon, label = "🔴", "执行异常"
            all_healthy = False
        lines.append(f"{icon} {mode}：{label} · {age_seconds / 60:.0f} 分钟前")
        if status.get("reason"):
            lines.append(f"  原因：{redact_sensitive_text(status['reason'])}")
        source_health = status.get("source_health")
        if isinstance(source_health, dict):
            failed_core = []
            failed_optional = []
            for name, state in source_health.items():
                if (
                    not isinstance(state, dict)
                    or state.get("status") not in {"failed", "partial"}
                ):
                    continue
                target = (
                    failed_core
                    if state.get("criticality", source_criticality(str(name))) == "core"
                    else failed_optional
                )
                target.append(str(name))
            if failed_core:
                lines.append(f"  关键源异常：{', '.join(failed_core[:3])}")
                all_healthy = False
            if failed_optional:
                lines.append(f"  可选源降级：{', '.join(failed_optional[:3])}")
                optional_degraded = True

    overall = (
        "整体：🔴 需要检查"
        if not all_healthy
        else "整体：🟡 可选源降级"
        if optional_degraded
        else "整体：🟢 正常"
    )
    lines.insert(1, overall)
    lines.append("说明：即时失败提醒默认静默；详情仍保留在服务日志和此状态面板。")
    return "\n".join(lines)


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


def _is_status_callback(callback: dict[str, Any]) -> bool:
    """Allow status refreshes only in a configured bot chat."""
    from core.interaction_auth import is_authorized_interaction

    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    configured_chat_ids = {
        str(chat_id)
        for chat_id in (
            settings.RADAR_INTERACTION_CHAT_ID,
            settings.MARKET_INTERACTION_CHAT_ID,
        )
        if chat_id
    }
    if chat_id not in configured_chat_ids:
        return False
    return is_authorized_interaction(callback, private_chat_id=chat_id)


def _handle_status_callback(
    callback: dict[str, Any], now: datetime, *, token: str | None = None
) -> str:
    if not _is_status_callback(callback):
        return "此按钮仅允许配置的管理员在监控频道使用。"
    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    message_id = message.get("message_id")
    if message_id is None:
        log_error("❌ 监控状态按钮缺少 Telegram message_id")
        return "状态面板已失效，请重新创建。"
    if _telegram_post(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": _format_status_message(now),
            "disable_web_page_preview": True,
            "reply_markup": _status_button_markup(),
        },
        token=token or settings.MARKET_INTERACTION_BOT_TOKEN,
    ) is None:
        return "状态发送失败，请查看服务日志。"
    return "监控状态已刷新。"


def send_status_panel() -> bool:
    """Post a pin-ready status panel to the monitoring chat once."""
    result = _telegram_post(
        "sendMessage",
        {
            "chat_id": settings.MARKET_INTERACTION_CHAT_ID,
            "text": _format_status_message(),
            "disable_web_page_preview": True,
            "reply_markup": _status_button_markup(),
        },
        token=settings.MARKET_INTERACTION_BOT_TOKEN,
    )
    if result is None:
        return False
    log_info("监控状态面板已发送；可在 Telegram 中置顶后随时点击刷新")
    return True


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


def _handle_callback(
    callback: dict[str, Any], now: datetime, *, token: str | None = None
) -> str:
    """Route only known button namespaces to their dedicated handlers."""
    data = str(callback.get("data") or "")
    if data == STATUS_CALLBACK_DATA:
        return _handle_status_callback(callback, now, token=token)
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
        settings.MARKET_INTERACTION_BOT_TOKEN
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
    write_service_heartbeat("telegram_listener")
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
                    notice = _handle_callback(
                        callback, datetime.now(settings.SHA_TZ), token=token
                    )
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
        write_service_heartbeat(
            "telegram_listener",
            status="partial" if polling_failed else "success",
            reason="Telegram polling failed" if polling_failed else "",
        )
        if polling_failed:
            time.sleep(5)
