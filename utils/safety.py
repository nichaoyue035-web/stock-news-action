from __future__ import annotations

from typing import Any

from config import settings


def redact_sensitive_text(text: Any, max_length: int = 160) -> str:
    """Return concise text with configured secrets redacted."""
    safe_text = str(text or "").replace("\n", " ").strip()
    for secret in (
        settings.DEEPSEEK_API_KEY,
        settings.TG_BOT_TOKEN,
        settings.TG_CHAT_ID,
        settings.TG_BOT_TOKEN_MONITOR,
        settings.TG_CHAT_ID_MONITOR,
        settings.TG_BOT_TOKEN_FUNDS,
        settings.TG_CHAT_ID_FUNDS,
    ):
        if secret:
            safe_text = safe_text.replace(str(secret), "<redacted>")
    return safe_text[:max_length] or "未知原因"
