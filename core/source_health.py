"""Per-run source-health records shared by all data providers."""

from __future__ import annotations

from typing import Any, Optional

from utils.safety import redact_sensitive_text


DATA_SOURCE_HEALTH: dict[str, dict[str, Any]] = {}


def redact_error_detail(text: Any) -> str:
    """Return a short error detail without leaking configured secrets."""
    return redact_sensitive_text(text, max_length=120)


def reset_data_source_health() -> None:
    """Clear per-run data source health records."""
    DATA_SOURCE_HEALTH.clear()


def record_data_source_health(
    name: str, status: str, detail: Any = "", count: Optional[int] = None
) -> None:
    """Record one concise data source status for fallback health messages."""
    DATA_SOURCE_HEALTH[name] = {
        "status": status,
        "detail": redact_error_detail(detail),
        "count": count,
    }


def get_data_source_health() -> dict[str, dict[str, Any]]:
    """Return a shallow copy of current data source health records."""
    return {name: dict(state) for name, state in DATA_SOURCE_HEALTH.items()}
