from __future__ import annotations

import csv
from datetime import datetime

import pytest

from config import settings
from core.analyzers import swing
from core.analyzers.review import _calculate_forward_returns
from core.history import _append_history
from core import market_data
from core import runtime


def _uptrend_bars() -> list[dict[str, float | str]]:
    bars = []
    for index in range(65):
        bars.append(
            {
                "date": f"2026-05-{index + 1:02d}",
                "close": 10.0 + index * 0.08,
                "volume": 100.0 if index < 60 else 180.0,
            }
        )
    return bars


def test_medium_term_candidate_requires_trend_volume_and_named_news():
    candidate = swing._build_candidate(
        {"name": "测试科技", "code": "000001"},
        _uptrend_bars(),
        [{"title": "测试科技披露新订单", "source": "公告", "link": "https://example.test"}],
    )

    assert candidate is not None
    assert candidate["code"] == "000001"
    assert candidate["return_20"] >= settings.SWING_MIN_20D_RETURN_PCT
    assert candidate["return_60"] >= settings.SWING_MIN_60D_RETURN_PCT
    assert candidate["volume_ratio"] >= settings.SWING_MIN_VOLUME_RATIO


def test_medium_term_candidate_skips_stock_without_named_evidence():
    assert (
        swing._build_candidate(
            {"name": "测试科技", "code": "000001"},
            _uptrend_bars(),
            [{"title": "行业出现新订单", "source": "公告"}],
        )
        is None
    )


def test_active_medium_term_observation_blocks_reselection(monkeypatch, tmp_path):
    pick_file = tmp_path / "stock_pick.json"
    monkeypatch.setattr(settings, "PICK_FILE", str(pick_file))
    now = datetime(2026, 7, 28, 15, 20, tzinfo=settings.SHA_TZ)
    assert swing._save_pick(
        {
            "name": "测试科技",
            "code": "000001",
            "strategy": "medium_term",
            "selected_at": now.isoformat(),
        }
    )

    assert swing._load_active_observation(now) == {
        "name": "测试科技",
        "code": "000001",
        "strategy": "medium_term",
        "selected_at": now.isoformat(),
    }


def test_history_records_medium_term_strategy(monkeypatch, tmp_path):
    history_file = tmp_path / "history.csv"
    monkeypatch.setattr(settings, "HISTORY_FILE", str(history_file))

    assert _append_history(
        {
            "name": "测试科技",
            "code": "000001",
            "reason": "中期趋势成立",
            "strategy": "medium_term",
            "observation_days": 45,
        },
        "12.34",
    )

    with open(history_file, newline="", encoding="utf-8") as file:
        row = next(csv.DictReader(file))
    assert row["Strategy"] == "medium_term"
    assert row["Observation_Days"] == "45"


def test_medium_term_selection_sends_one_pick_and_records_it(monkeypatch, tmp_path):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, _timezone=None):
            return cls(2026, 7, 28, 15, 20, tzinfo=settings.SHA_TZ)

    pick_file = tmp_path / "stock_pick.json"
    history_file = tmp_path / "history.csv"
    monkeypatch.setattr(settings, "PICK_FILE", str(pick_file))
    monkeypatch.setattr(settings, "HISTORY_FILE", str(history_file))
    monkeypatch.setattr(swing, "datetime", FixedDatetime)
    monkeypatch.setattr(swing, "is_cn_a_share_trading_day", lambda _now: True)
    monkeypatch.setattr(
        swing,
        "get_hot_stocks_data",
        lambda: [{"name": "测试科技", "code": "000001"}],
    )
    monkeypatch.setattr(swing, "get_stock_history_bars", lambda *_args: _uptrend_bars())
    monkeypatch.setattr(
        swing,
        "get_news",
        lambda *_args, **_kwargs: [
            {"title": "测试科技披露新订单", "source": "公告", "link": "https://example.test"}
        ],
    )
    sent = []
    monkeypatch.setattr(runtime, "send_tg", lambda text, **_kwargs: sent.append(text) or True)

    swing.run_swing()

    assert len(sent) == 1
    assert "中期观察标的" in sent[0]
    assert '"strategy": "medium_term"' in pick_file.read_text(encoding="utf-8")
    assert "medium_term" in history_file.read_text(encoding="utf-8")


def test_medium_term_review_uses_20_and_40_session_returns():
    closes = [{"close": 10 + index * 0.1} for index in range(40)]

    returns = _calculate_forward_returns(10.0, closes, (20, 40))

    assert returns[20] == pytest.approx(19.0)
    assert returns[40] == pytest.approx(39.0)


def test_stock_history_bars_reads_recent_daily_bars(monkeypatch):
    requested = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "klines": [
                        "2026-07-27,10.00,10.20,10.30,9.90,12345",
                        "2026-07-28,10.20,10.50,10.60,10.10,23456",
                    ]
                }
            }

    def fake_get(*_args, **kwargs):
        requested.update(kwargs["params"])
        return Response()

    monkeypatch.setattr(market_data, "request_get", fake_get)
    bars = market_data.get_stock_history_bars("000001", "2026-07-28", 2)

    assert bars == [
        {"date": "2026-07-27", "close": 10.2, "volume": 12345.0},
        {"date": "2026-07-28", "close": 10.5, "volume": 23456.0},
    ]
    assert requested["klt"] == "101"
    assert requested["end"] == "20260728"
