from __future__ import annotations

from datetime import datetime, timedelta

from config import settings
from core import radar
from core.data_fetcher import get_us_stock_snapshots
from core.radar_store import RadarStore


def _a_share_time() -> datetime:
    return datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)


def _make_store(tmp_path, monkeypatch) -> RadarStore:
    database = tmp_path / "radar.db"
    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(database))
    store = RadarStore(str(database))
    store.initialize()
    return store


def _create_candidate(store: RadarStore, now: datetime) -> dict:
    candidate, created = store.create_candidate(
        market="CN",
        symbol="000001",
        name="测试股",
        price=10.0,
        pct=1.0,
        volume=None,
        attributes={"signal": "盘中快速上涨", "evidence": "测试"},
        now=now,
        initial_track_minutes=10,
    )
    assert created is True
    store.set_telegram_delivery(candidate["candidate_id"], "123", 456, now)
    return candidate


def test_a_share_radar_creates_candidate_only_after_a_quote_baseline(
    monkeypatch, tmp_path
):
    store = _make_store(tmp_path, monkeypatch)
    now = _a_share_time()
    quotes = iter(
        [
            {"name": "测试股", "price": "10.00", "pct": "1.00"},
            {"name": "测试股", "price": "10.20", "pct": "3.00"},
        ]
    )
    monkeypatch.setattr(settings, "RADAR_A_SHARE_CODES", ["000001"])
    monkeypatch.setattr(settings, "RADAR_A_SHARE_MINUTE_CHANGE_PCT", 1.5)
    monkeypatch.setattr(settings, "INTERACTION_BOT_TOKEN", "token")
    monkeypatch.setattr(settings, "INTERACTION_CHAT_ID", "123")
    monkeypatch.setattr(radar, "get_stock_quote", lambda code: next(quotes))
    monkeypatch.setattr(radar, "send_tg_interactive", lambda *args, **kwargs: 456)

    assert radar._scan_a_share_candidates(store, now) == (1, 0)
    assert radar._scan_a_share_candidates(store, now + timedelta(minutes=1)) == (1, 1)

    candidates = store.active_candidates(now + timedelta(minutes=1))
    assert len(candidates) == 1
    assert candidates[0]["market"] == "CN"
    assert candidates[0]["attributes"]["signal"] == "盘中快速上涨"


def test_callback_extends_only_for_an_allowed_user(monkeypatch, tmp_path):
    store = _make_store(tmp_path, monkeypatch)
    now = _a_share_time()
    candidate = _create_candidate(store, now)
    monkeypatch.setattr(settings, "INTERACTION_ALLOWED_USER_IDS", [999])
    monkeypatch.setattr(settings, "INTERACTION_CHAT_ID", "123")

    notice = radar.handle_radar_callback(
        {
            "data": f"radar:{candidate['candidate_id']}:120",
            "from": {"id": 999},
            "message": {"chat": {"id": 123, "type": "private"}},
        },
        now + timedelta(minutes=1),
    )

    assert "120 分钟" in notice
    updated = store.get_candidate(candidate["candidate_id"])
    assert updated["status"] == "tracking"

    notice = radar.handle_radar_callback(
        {
            "data": f"radar:{candidate['candidate_id']}:stop",
            "from": {"id": 1000},
            "message": {"chat": {"id": 123, "type": "private"}},
        },
        now + timedelta(minutes=2),
    )
    assert "仅允许" in notice
    assert store.get_candidate(candidate["candidate_id"])["status"] == "tracking"


def test_listener_replies_to_private_id_request_without_authorizing_user(monkeypatch):
    from core import telegram_interaction

    calls = []
    monkeypatch.setattr(
        telegram_interaction,
        "_telegram_post",
        lambda method, payload: calls.append((method, payload)) or {},
    )
    private_message = {
        "text": "/id",
        "from": {"id": 999},
        "chat": {"id": 999, "type": "private"},
    }
    group_message = {
        "text": "/id",
        "from": {"id": 999},
        "chat": {"id": -100, "type": "supergroup"},
    }

    assert telegram_interaction._handle_private_id_command(private_message) is True
    assert telegram_interaction._handle_private_id_command(group_message) is False
    assert calls == [
        (
            "sendMessage",
            {
                "chat_id": "999",
                "text": "你的 Telegram 数字 ID：999\n请将这串数字提供给管理员，以启用群组里的事件跟踪按钮。",
            },
        )
    ]


def test_active_candidate_stops_after_price_invalidation(monkeypatch, tmp_path):
    store = _make_store(tmp_path, monkeypatch)
    now = _a_share_time()
    candidate = _create_candidate(store, now)
    monkeypatch.setattr(settings, "RADAR_INVALIDATION_PCT", 3.0)
    monkeypatch.setattr(
        radar,
        "_fetch_candidate_quote",
        lambda _: {"name": "测试股", "price": 9.6, "pct": -3.0, "volume": None},
    )
    monkeypatch.setattr(radar, "_send_tg_with_summary", lambda *args, **kwargs: True)

    processed, confirmed, invalidated = radar._process_active_candidates(
        store, now + timedelta(minutes=1)
    )

    assert processed == 1
    assert confirmed == 0
    assert invalidated == 1
    assert store.get_candidate(candidate["candidate_id"])["status"] == "closed"


def test_polygon_snapshot_normalizes_a_candidate_quote(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "tickers": [
                    {
                        "ticker": "TEST",
                        "lastTrade": {"p": 2.5},
                        "day": {"c": 2.5, "v": 1_000_000},
                        "todaysChangePerc": 12.3,
                    }
                ]
            }

    monkeypatch.setattr(settings, "POLYGON_API_KEY", "test-key")
    monkeypatch.setattr(
        "core.data_fetcher.requests.get", lambda *args, **kwargs: Response()
    )

    snapshots = get_us_stock_snapshots()

    assert snapshots == [
        {
            "symbol": "TEST",
            "name": "TEST",
            "price": 2.5,
            "pct": 12.3,
            "volume": 1_000_000.0,
            "dollar_volume": 2_500_000.0,
            "source": "polygon",
        }
    ]
