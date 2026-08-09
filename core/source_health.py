"""Per-run source-health records shared by all data providers."""

from __future__ import annotations

from typing import Any, Optional

from utils.safety import redact_sensitive_text


DATA_SOURCE_HEALTH: dict[str, dict[str, Any]] = {}

# A failed optional discovery or enrichment source must remain visible without
# declaring the entire market-monitoring pipeline unavailable.  Sources not in
# this set are deliberately treated as optional until they are promoted through
# an explicit production decision.
CORE_SOURCE_NAMES = frozenset(
    {
        "东方财富快讯",
        "海外 RSS",
        "新闻合并处理",
        "资金流数据",
        "热门股数据",
        "个股行情",
        "历史行情",
        "Polygon 美股行情",
        "Polygon 美股新闻",
    }
)


def source_criticality(name: str) -> str:
    """Return the operational importance assigned to one named source."""
    return "core" if str(name) in CORE_SOURCE_NAMES else "optional"


def has_critical_source_failure(health: dict[str, dict[str, Any]]) -> bool:
    """Return whether a failed source makes the current task materially degraded."""
    return any(
        state.get("criticality", source_criticality(name)) == "core"
        and state.get("status") in {"failed", "partial"}
        for name, state in health.items()
        if isinstance(state, dict)
    )


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
        "criticality": source_criticality(name),
    }


def get_data_source_health() -> dict[str, dict[str, Any]]:
    """Return a shallow copy of current data source health records."""
    return {name: dict(state) for name, state in DATA_SOURCE_HEALTH.items()}
