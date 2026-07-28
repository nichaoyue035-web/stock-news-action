"""Persistent state for the interactive A-share and US-stock radar."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


ACTIVE_CANDIDATE_STATUSES = ("auto_tracking", "tracking", "confirmed")


def _as_utc_text(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _from_utc_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _row_to_candidate(row: sqlite3.Row) -> dict[str, Any]:
    candidate = dict(row)
    candidate["attributes"] = json.loads(candidate.pop("attributes_json") or "{}")
    return candidate


class RadarStore:
    """Store quote baselines, active candidates and Telegram callback state."""

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
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS radar_quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    price REAL NOT NULL,
                    pct REAL,
                    volume REAL,
                    UNIQUE(market, symbol, observed_at)
                );

                CREATE INDEX IF NOT EXISTS idx_radar_quotes_symbol_time
                ON radar_quotes(market, symbol, observed_at DESC);

                CREATE TABLE IF NOT EXISTS radar_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    initial_price REAL NOT NULL,
                    initial_pct REAL,
                    initial_volume REAL,
                    last_price REAL,
                    last_pct REAL,
                    last_observed_at TEXT,
                    attributes_json TEXT NOT NULL,
                    confirmation_sent INTEGER NOT NULL DEFAULT 0,
                    telegram_chat_id TEXT,
                    telegram_message_id INTEGER,
                    updated_at TEXT NOT NULL,
                    closed_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_radar_candidates_active
                ON radar_candidates(status, expires_at, market, symbol);

                CREATE TABLE IF NOT EXISTS radar_locks (
                    lock_name TEXT PRIMARY KEY,
                    acquired_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_update_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def acquire_lock(
        self, lock_name: str, now: datetime, stale_after_minutes: int = 5
    ) -> bool:
        now_text = _as_utc_text(now)
        stale_before = _as_utc_text(
            now - timedelta(minutes=max(1, stale_after_minutes))
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT acquired_at FROM radar_locks WHERE lock_name = ?", (lock_name,)
            ).fetchone()
            if existing and str(existing["acquired_at"]) > stale_before:
                return False
            connection.execute(
                """
                INSERT OR REPLACE INTO radar_locks (lock_name, acquired_at)
                VALUES (?, ?)
                """,
                (lock_name, now_text),
            )
            return True

    def release_lock(self, lock_name: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM radar_locks WHERE lock_name = ?", (lock_name,)
            )

    def record_quote(
        self,
        *,
        market: str,
        symbol: str,
        name: str,
        price: float,
        pct: Optional[float],
        volume: Optional[float],
        observed_at: datetime,
        max_gap_minutes: int = 3,
    ) -> Optional[dict[str, Any]]:
        observed_text = _as_utc_text(observed_at)
        cutoff_text = _as_utc_text(
            observed_at - timedelta(minutes=max(1, max_gap_minutes))
        )
        with self._connect() as connection:
            previous = connection.execute(
                """
                SELECT name, observed_at, price, pct, volume
                FROM radar_quotes
                WHERE market = ? AND symbol = ?
                    AND observed_at >= ? AND observed_at < ?
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (market, symbol, cutoff_text, observed_text),
            ).fetchone()
            connection.execute(
                """
                INSERT OR REPLACE INTO radar_quotes (
                    market, symbol, name, observed_at, price, pct, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (market, symbol, name, observed_text, price, pct, volume),
            )
        return dict(previous) if previous else None

    def create_candidate(
        self,
        *,
        market: str,
        symbol: str,
        name: str,
        price: float,
        pct: Optional[float],
        volume: Optional[float],
        attributes: dict[str, Any],
        now: datetime,
        initial_track_minutes: int,
    ) -> tuple[dict[str, Any], bool]:
        """Create one active candidate per market/symbol, otherwise reuse it."""
        now_text = _as_utc_text(now)
        expires_text = _as_utc_text(
            now + timedelta(minutes=max(1, initial_track_minutes))
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ", ".join("?" for _ in ACTIVE_CANDIDATE_STATUSES)
            existing = connection.execute(
                f"""
                SELECT * FROM radar_candidates
                WHERE market = ? AND symbol = ?
                    AND status IN ({placeholders})
                    AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (market, symbol, *ACTIVE_CANDIDATE_STATUSES, now_text),
            ).fetchone()
            if existing:
                return _row_to_candidate(existing), False

            candidate_id = uuid.uuid4().hex[:12]
            connection.execute(
                """
                INSERT INTO radar_candidates (
                    candidate_id, market, symbol, name, status, created_at, expires_at,
                    initial_price, initial_pct, initial_volume, last_price, last_pct,
                    last_observed_at, attributes_json, updated_at
                ) VALUES (?, ?, ?, ?, 'auto_tracking', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    market,
                    symbol,
                    name,
                    now_text,
                    expires_text,
                    price,
                    pct,
                    volume,
                    price,
                    pct,
                    now_text,
                    _safe_json(attributes),
                    now_text,
                ),
            )
            created = connection.execute(
                "SELECT * FROM radar_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return _row_to_candidate(created), True

    def active_candidates(self, now: datetime) -> list[dict[str, Any]]:
        now_text = _as_utc_text(now)
        placeholders = ", ".join("?" for _ in ACTIVE_CANDIDATE_STATUSES)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM radar_candidates
                WHERE status IN ({placeholders}) AND expires_at > ?
                ORDER BY created_at ASC
                """,
                (*ACTIVE_CANDIDATE_STATUSES, now_text),
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def has_active_candidate(self, market: str, symbol: str, now: datetime) -> bool:
        now_text = _as_utc_text(now)
        placeholders = ", ".join("?" for _ in ACTIVE_CANDIDATE_STATUSES)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT 1 FROM radar_candidates
                WHERE market = ? AND symbol = ?
                    AND status IN ({placeholders}) AND expires_at > ?
                LIMIT 1
                """,
                (market, symbol, *ACTIVE_CANDIDATE_STATUSES, now_text),
            ).fetchone()
        return row is not None

    def expiring_candidates(self, now: datetime) -> list[dict[str, Any]]:
        now_text = _as_utc_text(now)
        placeholders = ", ".join("?" for _ in ACTIVE_CANDIDATE_STATUSES)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM radar_candidates
                WHERE status IN ({placeholders}) AND expires_at <= ?
                ORDER BY expires_at ASC
                """,
                (*ACTIVE_CANDIDATE_STATUSES, now_text),
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def update_quote(
        self,
        candidate_id: str,
        *,
        price: float,
        pct: Optional[float],
        observed_at: datetime,
    ) -> None:
        timestamp = _as_utc_text(observed_at)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE radar_candidates
                SET last_price = ?, last_pct = ?, last_observed_at = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (price, pct, timestamp, timestamp, candidate_id),
            )

    def mark_confirmed(self, candidate_id: str, now: datetime) -> None:
        timestamp = _as_utc_text(now)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE radar_candidates
                SET status = 'confirmed', confirmation_sent = 1, updated_at = ?
                WHERE candidate_id = ? AND status = 'auto_tracking'
                """,
                (timestamp, candidate_id),
            )

    def close_candidate(self, candidate_id: str, reason: str, now: datetime) -> bool:
        timestamp = _as_utc_text(now)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE radar_candidates
                SET status = 'closed', closed_reason = ?, updated_at = ?
                WHERE candidate_id = ? AND status IN ('auto_tracking', 'tracking', 'confirmed')
                """,
                (reason[:160], timestamp, candidate_id),
            )
        return cursor.rowcount == 1

    def extend_candidate(
        self, candidate_id: str, minutes: int, now: datetime
    ) -> Optional[dict[str, Any]]:
        expiry = _as_utc_text(now + timedelta(minutes=max(1, minutes)))
        timestamp = _as_utc_text(now)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE radar_candidates
                SET status = 'tracking', expires_at = ?, updated_at = ?
                WHERE candidate_id = ?
                    AND status IN ('auto_tracking', 'tracking', 'confirmed')
                    AND expires_at > ?
                """,
                (expiry, timestamp, candidate_id, timestamp),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM radar_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return _row_to_candidate(row) if row else None

    def get_candidate(self, candidate_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM radar_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return _row_to_candidate(row) if row else None

    def set_telegram_delivery(
        self, candidate_id: str, chat_id: str, message_id: int, now: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE radar_candidates
                SET telegram_chat_id = ?, telegram_message_id = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (chat_id, message_id, _as_utc_text(now), candidate_id),
            )

    def last_telegram_update_id(self) -> Optional[int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_value FROM telegram_update_state
                WHERE state_key = 'last_update_id'
                """
            ).fetchone()
        try:
            return int(row["state_value"]) if row else None
        except (TypeError, ValueError):
            return None

    def set_last_telegram_update_id(self, update_id: int, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO telegram_update_state (
                    state_key, state_value, updated_at
                ) VALUES ('last_update_id', ?, ?)
                """,
                (str(update_id), _as_utc_text(now)),
            )
