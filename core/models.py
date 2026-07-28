from __future__ import annotations

import datetime
from typing import Any, TypedDict


class NewsItem(TypedDict):
    title: str
    digest: str
    link: str
    time_str: str
    datetime: datetime.datetime
    source: str
    summary: str
    url: str
    published_at: str
    category: str
    importance: str
    market_scope: str
    related_sectors: list[str]


def validate_news_item(item: dict[str, Any]) -> tuple[bool, str]:
    """Validate fields required by analyzers before a news item is admitted."""
    if not isinstance(item, dict):
        return False, "not a mapping"
    if not str(item.get("title") or "").strip():
        return False, "missing title"
    if not isinstance(item.get("datetime"), datetime.datetime):
        return False, "missing datetime"
    if not str(item.get("source") or "").strip():
        return False, "missing source"
    return True, ""
