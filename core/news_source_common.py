"""Shared parsing helpers for external news providers."""

from __future__ import annotations

import datetime
import email.utils
import re
from typing import Any, Optional

from config import settings


def _strip_html(text: Any) -> str:
    """去除 HTML 标签并返回字符串。"""
    return re.sub(r"<[^>]+>", "", str(text or ""))


def _parse_datetime(raw_value: Any) -> Optional[datetime.datetime]:
    """解析常见日期格式并转换为上海时区。"""
    text = str(raw_value or "").strip()
    if not text:
        return None

    try:
        iso_value = text.replace("Z", "+00:00")
        parsed = datetime.datetime.fromisoformat(iso_value)
        return (
            parsed.replace(tzinfo=settings.SHA_TZ)
            if parsed.tzinfo is None
            else parsed.astimezone(settings.SHA_TZ)
        )
    except ValueError:
        pass

    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None

    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=settings.SHA_TZ)
        return parsed.astimezone(settings.SHA_TZ)

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y%m%dT%H%M%SZ",
    ):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return (
                dt.replace(tzinfo=settings.SHA_TZ)
                if dt.tzinfo is None
                else dt.astimezone(settings.SHA_TZ)
            )
        except ValueError:
            continue
    return None
