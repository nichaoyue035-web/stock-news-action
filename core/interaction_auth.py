"""Shared authorization rule for Telegram callback buttons."""

from __future__ import annotations

from typing import Any

from config import settings


def is_authorized_interaction(callback: dict[str, Any]) -> bool:
    """Allow only configured users, with a safe private-chat fallback."""
    sender = callback.get("from") if isinstance(callback.get("from"), dict) else {}
    user_id = sender.get("id")
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    if user_id in settings.INTERACTION_ALLOWED_USER_IDS:
        return True

    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    return (
        chat.get("type") == "private"
        and chat_id == str(settings.INTERACTION_CHAT_ID)
        and chat_id == str(user_id)
    )
