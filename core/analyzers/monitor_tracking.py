"""User-controlled follow-up tracking for important monitor events."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Optional
from urllib.parse import urlparse

from config import settings
from core.interaction_auth import is_authorized_interaction
from core.monitor_store import MonitorStore


NEWS_TRACK_CALLBACK_PREFIX = "news"
NEWS_TRACK_MINUTES = 120
_NEWS_TRACK_STOP_WORDS = {
    "news",
    "market",
    "markets",
    "report",
    "reports",
    "said",
    "says",
    "will",
    "with",
    "from",
    "that",
    "this",
    "市场",
    "消息",
    "新闻",
    "全球",
    "重要",
    "最新",
    "报道",
    "表示",
    "发布",
    "公司",
    "中国",
    "美国",
    "相关",
    "事件",
    "风险",
    "影响",
}


def _compact_alert_text(value: Any, limit: int = 160) -> str:
    """Keep a factual line readable without inventing a summary."""
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _news_tracking_buttons(event_key: str, link: str) -> dict[str, Any]:
    """Build small, callback-safe controls for one important news event."""
    tracking_id = event_key[:16]
    rows: list[list[dict[str, str]]] = [
        [
            {
                "text": "继续跟踪 2 小时",
                "callback_data": f"{NEWS_TRACK_CALLBACK_PREFIX}:{tracking_id}:{NEWS_TRACK_MINUTES}",
            },
            {
                "text": "停止跟踪",
                "callback_data": f"{NEWS_TRACK_CALLBACK_PREFIX}:{tracking_id}:stop",
            },
        ]
    ]
    parsed = urlparse(str(link or ""))
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        rows.append([{"text": "查看原文", "url": str(link)}])
    return {"inline_keyboard": rows}


def _tracking_terms(value: Any) -> set[str]:
    """Extract conservative title-level terms without calling an AI model."""
    text = str(value or "").lower()
    words = {
        token
        for token in re.findall(r"[a-z][a-z0-9.-]{2,}", text)
        if token not in _NEWS_TRACK_STOP_WORDS
    }
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    words.update(
        chinese[index : index + 2]
        for index in range(max(0, len(chinese) - 1))
        if chinese[index : index + 2] not in _NEWS_TRACK_STOP_WORDS
    )
    return words


def _is_related_tracked_news(tracker: dict[str, Any], item: dict[str, Any]) -> bool:
    """Keep follow-ups conservative: two shared title-level terms are required."""
    if str(item.get("event_key") or "") == str(tracker.get("event_key") or ""):
        return False
    original_terms = _tracking_terms(tracker.get("title"))
    candidate_terms = _tracking_terms(item.get("title"))
    return len(original_terms.intersection(candidate_terms)) >= 2


def _format_news_tracking_update(
    tracker: dict[str, Any], related_items: list[dict[str, Any]]
) -> str:
    lines = [
        "🧭 事件有新进展",
        f"起点：{_compact_alert_text(tracker.get('title'), 160)}",
        "新消息：",
    ]
    for item in related_items[:2]:
        lines.append(
            f"- {_compact_alert_text(item.get('title'), 180)}"
            f"（{_compact_alert_text(item.get('source'), 60)}）"
        )
        link = str(item.get("link") or "").strip()
        if link:
            lines.append(f"  {link}")
    lines.extend(("按标题关联到的新来源；请以原文核实，未确认不等于事件升级。",))
    return "\n".join(lines)


def _format_news_tracking_end(tracker: dict[str, Any]) -> str:
    updates = int(tracker.get("update_count") or 0)
    return "\n".join(
        (
            "⚪️ 事件跟踪结束",
            _compact_alert_text(tracker.get("title"), 160),
            f"2 小时内推送了 {updates} 条相关进展。",
            "仅覆盖已接入新闻源；未发现更新不代表没有后续。",
        )
    )


def _process_active_news_trackers(
    store: MonitorStore, now: datetime
) -> tuple[int, int]:
    """Send verified-source follow-ups for user-enabled event tracking only."""
    from core.runtime import _send_tg_with_summary

    updates_sent = 0
    ended_sent = 0
    for tracker in store.active_news_trackers(now):
        since = str(
            tracker.get("last_checked_at")
            or tracker.get("activated_at")
            or tracker.get("created_at")
            or ""
        )
        related_items = [
            item
            for item in store.news_events_since(since, now)
            if not item.get("discovery_only")
            and _is_related_tracked_news(tracker, item)
        ]
        if related_items:
            sent = _send_tg_with_summary(
                _format_news_tracking_update(tracker, related_items),
                token=settings.TG_BOT_TOKEN_MONITOR,
                chat_id=settings.TG_CHAT_ID_MONITOR,
            )
            if not sent:
                continue
            store.mark_news_tracker_checked(
                str(tracker["tracking_id"]), now, len(related_items)
            )
            updates_sent += 1
        else:
            store.mark_news_tracker_checked(str(tracker["tracking_id"]), now)

    for tracker in store.expiring_news_trackers(now):
        sent = _send_tg_with_summary(
            _format_news_tracking_end(tracker),
            token=settings.TG_BOT_TOKEN_MONITOR,
            chat_id=settings.TG_CHAT_ID_MONITOR,
        )
        store.close_news_tracker(str(tracker["tracking_id"]), "追踪到期")
        ended_sent += int(sent)
    return updates_sent, ended_sent


def handle_news_tracking_callback(
    callback: dict[str, Any], now: Optional[datetime] = None
) -> str:
    """Apply one event-tracking button click without triggering any trade action."""
    now = now or datetime.now(settings.SHA_TZ)
    if not is_authorized_interaction(
        callback, private_chat_id=settings.MARKET_INTERACTION_CHAT_ID
    ):
        return "此按钮仅允许配置的管理员使用。"
    parts = str(callback.get("data") or "").split(":")
    if len(parts) != 3 or parts[0] != NEWS_TRACK_CALLBACK_PREFIX:
        return "未知的事件跟踪操作。"
    tracking_id, action = parts[1], parts[2]
    store = MonitorStore(settings.MONITOR_DB_FILE)
    store.initialize()
    tracker = store.get_news_tracker(tracking_id)
    if tracker is None:
        return "该事件已过期或不存在。"
    message = (
        callback.get("message") if isinstance(callback.get("message"), dict) else {}
    )
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if str(chat.get("id") or "") != str(tracker.get("telegram_chat_id") or ""):
        return "该按钮不属于当前提醒消息。"
    if action == "stop":
        if not store.close_news_tracker(tracking_id, "用户停止追踪"):
            return "该事件跟踪已经结束。"
        from core.metrics import record_feedback_metric

        record_feedback_metric("news", "stop")
        return "已停止该事件的后续跟踪。"
    if action != str(NEWS_TRACK_MINUTES):
        return "不允许的事件跟踪时长。"
    if not store.activate_news_tracker(tracking_id, NEWS_TRACK_MINUTES, now):
        return "该事件跟踪已经结束。"
    from core.metrics import record_feedback_metric

    record_feedback_metric("news", "continue_tracking")
    return "已开启 2 小时事件跟踪；系统按监控周期核对已接入新闻源，有新的相关标题才会推送。"
