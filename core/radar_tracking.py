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


def process_active_candidates(
    store: RadarStore,
    now: datetime,
    *,
    is_a_share_trading_session: SessionCheck,
    is_us_trading_session: SessionCheck,
    fetch_candidate_quote: FetchCandidateQuote,
    safe_float: SafeFloat,
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
                send_update(candidate, "invalidated")
                invalidated += 1
            continue

        created_at = datetime.fromisoformat(str(candidate["created_at"]))
        age = now.astimezone(created_at.tzinfo) - created_at
        if (
            candidate["status"] == "auto_tracking"
            and age >= timedelta(minutes=settings.RADAR_CONFIRM_AFTER_MINUTES)
        ):
            store.mark_confirmed(str(candidate["candidate_id"]), now)
            send_update(candidate, "confirmed")
            confirmed += 1
    return processed, confirmed, invalidated


def close_expired_candidates(
    store: RadarStore, now: datetime, *, send_update: SendUpdate
) -> int:
    closed = 0
    for candidate in store.expiring_candidates(now):
        if store.close_candidate(str(candidate["candidate_id"]), "追踪到期", now):
            send_update(candidate, "expired")
            closed += 1
    return closed
