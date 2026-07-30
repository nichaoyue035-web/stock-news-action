"""Lifecycle tracking for radar candidates after their initial alert."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from config import settings
from core.radar_store import RadarStore
from utils.notifier import log_info


FetchCandidateQuote = Callable[[dict[str, Any]], Optional[dict[str, Any]]]
SessionCheck = Callable[[datetime], bool]
SafeFloat = Callable[[Any], Optional[float]]
SendUpdate = Callable[[dict[str, Any], str], bool]
SendCandidate = Callable[[dict[str, Any]], bool]


def _holds_confirmation(candidate: dict[str, Any], change_from_initial: float) -> bool:
    """Reject a trigger that reverses materially during its silent window."""
    signal = str((candidate.get("attributes") or {}).get("signal") or "")
    reversal_limit = settings.RADAR_CONFIRM_MAX_REVERSAL_PCT
    if signal == "盘中快速下跌":
        return change_from_initial <= reversal_limit
    return change_from_initial >= -reversal_limit


def process_active_candidates(
    store: RadarStore,
    now: datetime,
    *,
    is_a_share_trading_session: SessionCheck,
    is_us_trading_session: SessionCheck,
    fetch_candidate_quote: FetchCandidateQuote,
    safe_float: SafeFloat,
    send_candidate: SendCandidate,
    send_update: SendUpdate,
) -> tuple[int, int, int]:
    confirmed = 0
    invalidated = 0
    processed = 0
    for candidate in store.active_candidates(now):
        should_fetch = (
            is_a_share_trading_session(now)
            if candidate["market"] == "CN"
            else is_us_trading_session(now)
        )
        if not should_fetch:
            continue
        quote = fetch_candidate_quote(candidate)
        if not quote:
            log_info(
                f"雷达追踪暂未获得有效报价: {candidate['market']}:{candidate['symbol']}"
            )
            continue
        price = float(quote["price"])
        pct = safe_float(quote.get("pct"))
        volume = safe_float(quote.get("volume"))
        store.record_quote(
            market=str(candidate["market"]),
            symbol=str(candidate["symbol"]),
            name=str(quote.get("name") or candidate["name"]),
            price=price,
            pct=pct,
            volume=volume,
            observed_at=now,
        )
        store.update_quote(
            str(candidate["candidate_id"]), price=price, pct=pct, observed_at=now
        )
        candidate["last_price"] = price
        candidate["last_pct"] = pct
        processed += 1

        change_from_initial = (price / float(candidate["initial_price"]) - 1) * 100
        if change_from_initial <= -settings.RADAR_INVALIDATION_PCT:
            if store.close_candidate(
                str(candidate["candidate_id"]), "触及价格失效条件", now
            ):
                if candidate.get("telegram_message_id"):
                    send_update(candidate, "invalidated")
                invalidated += 1
            continue

        created_at = datetime.fromisoformat(str(candidate["created_at"]))
        age = now.astimezone(created_at.tzinfo) - created_at
        if (
            candidate["status"] == "auto_tracking"
            and age >= timedelta(minutes=settings.RADAR_CONFIRM_AFTER_MINUTES)
        ):
            if not _holds_confirmation(candidate, change_from_initial):
                if store.close_candidate(
                    str(candidate["candidate_id"]), "确认期价格反转", now
                ):
                    invalidated += 1
                continue
            # Candidates created before confirmation-first delivery may already
            # have an initial Telegram message. Mark them confirmed without
            # adding a duplicate notification during the rollout.
            if candidate.get("telegram_message_id"):
                store.mark_confirmed(str(candidate["candidate_id"]), now)
                confirmed += 1
                continue
            confirmed_candidate = dict(candidate)
            confirmed_candidate["status"] = "confirmed"
            if send_candidate(confirmed_candidate):
                store.mark_confirmed(str(candidate["candidate_id"]), now)
                confirmed += 1
    return processed, confirmed, invalidated


def close_expired_candidates(store: RadarStore, now: datetime) -> int:
    closed = 0
    for candidate in store.expiring_candidates(now):
        if store.close_candidate(str(candidate["candidate_id"]), "追踪到期", now):
            closed += 1
    return closed
