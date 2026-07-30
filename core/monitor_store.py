"""SQLite-backed state for the real-time monitor.

The monitor runs repeatedly, so its deduplication and delivery state must outlive
one Python process.  This module deliberately uses the standard library only so
the monitor has no new runtime dependency.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def _as_utc_text(moment: datetime) -> str:
    """Return a consistently sortable UTC timestamp."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def news_event_key(item: dict[str, Any]) -> str:
    """Return a stable event key without relying on an optional provider ID."""
    published_at = item.get("datetime")
    published_text = (
        _as_utc_text(published_at)
        if isinstance(published_at, datetime)
        else str(item.get("time_str") or "")
    )
    identity = "|".join(
        (
            str(item.get("source") or "unknown").strip().lower(),
            str(item.get("link") or "").strip(),
            published_text,
            " ".join(str(item.get("title") or "").split()).lower(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class MonitorStore:
    """Persist monitor inputs and alert delivery attempts in one SQLite file."""

    def __init__(self, database_path: str):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self) -> None:
        """Create tables and indexes needed by the monitor."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS news_events (
                    event_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_time TEXT,
                    received_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    link TEXT NOT NULL,
                    category TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    market_scope TEXT NOT NULL,
                    related_sectors TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_news_events_received_at
                ON news_events(received_at);

                CREATE TABLE IF NOT EXISTS market_quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    price REAL NOT NULL,
                    pct REAL,
                    UNIQUE(code, observed_at)
                );

                CREATE INDEX IF NOT EXISTS idx_market_quotes_code_time
                ON market_quotes(code, observed_at DESC);

                CREATE TABLE IF NOT EXISTS monitor_alerts (
                    alert_key TEXT PRIMARY KEY,
                    dedup_key TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_monitor_alerts_dedup_sent
                ON monitor_alerts(dedup_key, status, sent_at DESC);

                CREATE TABLE IF NOT EXISTS news_trackers (
                    tracking_id TEXT PRIMARY KEY,
                    event_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    link TEXT NOT NULL,
                    telegram_chat_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    expires_at TEXT,
                    last_checked_at TEXT,
                    update_count INTEGER NOT NULL DEFAULT 0,
                    closed_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_news_trackers_active
                ON news_trackers(status, expires_at);

                CREATE TABLE IF NOT EXISTS monitor_locks (
                    lock_name TEXT PRIMARY KEY,
                    acquired_at TEXT NOT NULL
                );
                """
            )

    def acquire_lock(
        self, lock_name: str, now: datetime, stale_after_minutes: int = 10
    ) -> bool:
        """Claim a monitor-wide lock, replacing only an expired abandoned lock."""
        now_text = _as_utc_text(now)
        stale_before = _as_utc_text(
            now - timedelta(minutes=max(1, stale_after_minutes))
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT acquired_at FROM monitor_locks WHERE lock_name = ?",
                (lock_name,),
            ).fetchone()
            if existing and str(existing["acquired_at"]) > stale_before:
                return False
            connection.execute(
                """
                INSERT OR REPLACE INTO monitor_locks (lock_name, acquired_at)
                VALUES (?, ?)
                """,
                (lock_name, now_text),
            )
            return True

    def release_lock(self, lock_name: str) -> None:
        """Release a completed monitor cycle lock."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM monitor_locks WHERE lock_name = ?", (lock_name,)
            )

    def record_news_event(self, item: dict[str, Any], received_at: datetime) -> bool:
        """Store a raw news event once and return whether it was newly observed."""
        event_key = news_event_key(item)
        published_at = item.get("datetime")
        source_time = (
            _as_utc_text(published_at)
            if isinstance(published_at, datetime)
            else str(item.get("time_str") or "")
        )
        related_sectors = item.get("related_sectors")
        if not isinstance(related_sectors, list):
            related_sectors = []

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO news_events (
                    event_key, source, source_time, received_at, title, digest, link,
                    category, importance, market_scope, related_sectors, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    str(item.get("source") or "unknown"),
                    source_time,
                    _as_utc_text(received_at),
                    str(item.get("title") or ""),
                    str(item.get("digest") or ""),
                    str(item.get("link") or ""),
                    str(item.get("category") or "other"),
                    str(item.get("importance") or "medium"),
                    str(item.get("market_scope") or "其他"),
                    _safe_json(related_sectors),
                    _safe_json(item),
                ),
            )
            return cursor.rowcount == 1

    def offer_news_tracking(
        self,
        *,
        event_key: str,
        item: dict[str, Any],
        telegram_chat_id: str,
        now: datetime,
    ) -> str:
        """Persist a short callback-safe ID after an alert was delivered."""
        tracking_id = event_key[:16]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO news_trackers (
                    tracking_id, event_key, title, source, link, telegram_chat_id,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'offered', ?)
                """,
                (
                    tracking_id,
                    event_key,
                    str(item.get("title") or "未知事件"),
                    str(item.get("source") or "未知来源"),
                    str(item.get("link") or ""),
                    str(telegram_chat_id),
                    _as_utc_text(now),
                ),
            )
        return tracking_id

    def get_news_tracker(self, tracking_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM news_trackers WHERE tracking_id = ?", (tracking_id,)
            ).fetchone()
        return dict(row) if row else None

    def activate_news_tracker(
        self, tracking_id: str, minutes: int, now: datetime
    ) -> bool:
        """Start or extend a user-requested event tracking window."""
        now_text = _as_utc_text(now)
        expires_at = _as_utc_text(now + timedelta(minutes=max(1, minutes)))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE news_trackers
                SET status = 'tracking', activated_at = ?, expires_at = ?,
                    last_checked_at = ?, closed_reason = ''
                WHERE tracking_id = ? AND status IN ('offered', 'tracking')
                """,
                (now_text, expires_at, now_text, tracking_id),
            )
        return cursor.rowcount == 1

    def close_news_tracker(self, tracking_id: str, reason: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE news_trackers
                SET status = 'closed', closed_reason = ?
                WHERE tracking_id = ? AND status IN ('offered', 'tracking')
                """,
                (str(reason)[:120], tracking_id),
            )
        return cursor.rowcount == 1

    def active_news_trackers(self, now: datetime) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM news_trackers
                WHERE status = 'tracking' AND expires_at > ?
                ORDER BY activated_at ASC
                """,
                (_as_utc_text(now),),
            ).fetchall()
        return [dict(row) for row in rows]

    def expiring_news_trackers(self, now: datetime) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM news_trackers
                WHERE status = 'tracking' AND expires_at <= ?
                ORDER BY expires_at ASC
                """,
                (_as_utc_text(now),),
            ).fetchall()
        return [dict(row) for row in rows]

    def news_events_since(
        self, received_after: str, received_before: datetime
    ) -> list[dict[str, Any]]:
        """Return only already-recorded, newly received source items."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_key, title, source, link, payload
                FROM news_events
                WHERE received_at > ? AND received_at <= ?
                ORDER BY received_at ASC
                """,
                (str(received_after), _as_utc_text(received_before)),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update(
                {
                    "event_key": str(row["event_key"]),
                    "title": str(row["title"]),
                    "source": str(row["source"]),
                    "link": str(row["link"]),
                }
            )
            events.append(payload)
        return events

    def mark_news_tracker_checked(
        self, tracking_id: str, now: datetime, update_count: int = 0
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE news_trackers
                SET last_checked_at = ?, update_count = update_count + ?
                WHERE tracking_id = ? AND status = 'tracking'
                """,
                (_as_utc_text(now), max(0, int(update_count)), tracking_id),
            )

    def record_quote(
        self,
        *,
        code: str,
        name: str,
        price: float,
        pct: Optional[float],
        observed_at: datetime,
        max_gap_minutes: int,
    ) -> Optional[dict[str, Any]]:
        """Store one quote and return a recent prior quote for the same stock."""
        observed_text = _as_utc_text(observed_at)
        cutoff_text = _as_utc_text(
            observed_at - timedelta(minutes=max(1, max_gap_minutes))
        )
        with self._connect() as connection:
            previous = connection.execute(
                """
                SELECT name, observed_at, price, pct
                FROM market_quotes
                WHERE code = ? AND observed_at >= ? AND observed_at < ?
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (code, cutoff_text, observed_text),
            ).fetchone()
            connection.execute(
                """
                INSERT OR REPLACE INTO market_quotes (code, name, observed_at, price, pct)
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, name, observed_text, price, pct),
            )
        return dict(previous) if previous else None

    def claim_alert(
        self,
        *,
        alert_key: str,
        dedup_key: str,
        alert_type: str,
        severity: str,
        payload: dict[str, Any],
        now: datetime,
        cooldown_minutes: int = 0,
    ) -> bool:
        """Claim an alert for sending, allowing retries but suppressing duplicates."""
        now_text = _as_utc_text(now)
        stale_pending_before = _as_utc_text(now - timedelta(minutes=10))
        cooldown_before = _as_utc_text(
            now - timedelta(minutes=max(0, cooldown_minutes))
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status, updated_at FROM monitor_alerts WHERE alert_key = ?",
                (alert_key,),
            ).fetchone()
            if existing:
                if existing["status"] == "sent":
                    return False
                if (
                    existing["status"] == "pending"
                    and str(existing["updated_at"]) > stale_pending_before
                ):
                    return False
                connection.execute(
                    """
                    UPDATE monitor_alerts
                    SET status = 'pending', attempts = attempts + 1, updated_at = ?,
                        last_error = ''
                    WHERE alert_key = ?
                    """,
                    (now_text, alert_key),
                )
                return True

            if cooldown_minutes > 0:
                recent_sent = connection.execute(
                    """
                    SELECT 1 FROM monitor_alerts
                    WHERE dedup_key = ? AND status = 'sent' AND sent_at >= ?
                    LIMIT 1
                    """,
                    (dedup_key, cooldown_before),
                ).fetchone()
                if recent_sent:
                    return False

            connection.execute(
                """
                INSERT INTO monitor_alerts (
                    alert_key, dedup_key, alert_type, severity, payload, status,
                    attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 1, ?, ?)
                """,
                (
                    alert_key,
                    dedup_key,
                    alert_type,
                    severity,
                    _safe_json(payload),
                    now_text,
                    now_text,
                ),
            )
            return True

    def mark_alert_sent(self, alert_key: str, sent_at: datetime) -> None:
        """Mark a Telegram delivery as successful only after the API call succeeds."""
        timestamp = _as_utc_text(sent_at)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE monitor_alerts
                SET status = 'sent', sent_at = ?, updated_at = ?, last_error = ''
                WHERE alert_key = ?
                """,
                (timestamp, timestamp, alert_key),
            )

    def mark_alert_failed(self, alert_key: str, failed_at: datetime, error: str) -> None:
        """Keep failed sends retryable and retain only a safe, concise error detail."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE monitor_alerts
                SET status = 'failed', updated_at = ?, last_error = ?
                WHERE alert_key = ?
                """,
                (_as_utc_text(failed_at), str(error or "未知错误")[:160], alert_key),
            )

    def recent_sent_alert_payloads(
        self,
        *,
        alert_type: str,
        severity: str,
        now: datetime,
        lookback_minutes: int,
    ) -> list[dict[str, Any]]:
        """Return recent successful payloads for deterministic pre-send deduplication."""
        cutoff = _as_utc_text(now - timedelta(minutes=max(1, lookback_minutes)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM monitor_alerts
                WHERE alert_type = ? AND severity = ? AND status = 'sent'
                    AND sent_at >= ?
                ORDER BY sent_at DESC
                LIMIT 100
                """,
                (alert_type, severity, cutoff),
            ).fetchall()

        payloads: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def prune(self, now: datetime, retention_days: int) -> dict[str, int]:
        """Remove only expired monitor history, never active event tracking."""
        cutoff = _as_utc_text(now - timedelta(days=max(1, retention_days)))
        with self._connect() as connection:
            deleted = {
                "news_events": connection.execute(
                    "DELETE FROM news_events WHERE received_at < ?", (cutoff,)
                ).rowcount,
                "market_quotes": connection.execute(
                    "DELETE FROM market_quotes WHERE observed_at < ?", (cutoff,)
                ).rowcount,
                "monitor_alerts": connection.execute(
                    "DELETE FROM monitor_alerts WHERE updated_at < ?", (cutoff,)
                ).rowcount,
                "news_trackers": connection.execute(
                    """
                    DELETE FROM news_trackers
                    WHERE status != 'tracking' AND created_at < ?
                    """,
                    (cutoff,),
                ).rowcount,
            }
        return {name: max(0, count) for name, count in deleted.items()}
