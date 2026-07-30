"""Offline response replays for the external-provider shapes we depend on."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import settings


FIXTURES = Path(__file__).parent / "fixtures" / "external"


class ReplayResponse:
    """Small requests-compatible response backed by a checked-in fixture."""

    status_code = 200

    def __init__(self, *, text: str = "", content: bytes = b"") -> None:
        self.text = text
        self.content = content or text.encode("utf-8")

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        return None


def test_eastmoney_news_replay_preserves_parseable_item(monkeypatch):
    import core.data_fetcher as data_fetcher

    payload = (FIXTURES / "eastmoney_news.json").read_text(encoding="utf-8")
    monkeypatch.setattr(
        data_fetcher.requests,
        "get",
        lambda *_args, **_kwargs: ReplayResponse(text=f"callback({payload})"),
    )

    items = data_fetcher._fetch_eastmoney_news(minutes_lookback=None)

    assert items == [
        {
            "title": "测试快讯标题：政策发布",
            "digest": "用于离线回放的东方财富快讯正文。",
            "link": "https://kuaixun.eastmoney.com/test-item",
            "time_str": "09:30",
            "datetime": items[0]["datetime"],
            "source": "eastmoney",
        }
    ]


def test_atom_rss_replay_keeps_namespaced_entry(monkeypatch):
    import core.data_fetcher as data_fetcher

    content = (FIXTURES / "reuters_atom.xml").read_bytes()
    monkeypatch.setattr(settings, "EXTERNAL_NEWS_RSS", ["https://www.reuters.com/feed"])
    monkeypatch.setattr(settings, "GLOBAL_NEWS_RSS", "https://www.reuters.com/feed")
    monkeypatch.setattr(
        data_fetcher.requests,
        "get",
        lambda *_args, **_kwargs: ReplayResponse(content=content),
    )

    items = data_fetcher._fetch_external_rss_news(minutes_lookback=None)

    assert items[0]["title"] == "Replay central-bank update"
    assert items[0]["link"] == "https://www.reuters.com/world/replay-item"
    assert items[0]["source"] == "www.reuters.com"


def test_compact_utc_timestamp_is_converted_to_shanghai_time():
    from core.news_source_common import _parse_datetime

    parsed = _parse_datetime("20260730T183044Z")

    assert parsed is not None
    assert parsed.isoformat() == "2026-07-31T02:30:44+08:00"


def test_polygon_snapshot_replay_preserves_provider_neutral_quote(monkeypatch):
    import core.market_data as market_data

    payload = (FIXTURES / "polygon_snapshot.json").read_text(encoding="utf-8")
    monkeypatch.setattr(settings, "POLYGON_API_KEY", "test-key")
    monkeypatch.setattr(
        market_data,
        "request_get",
        lambda *_args, **_kwargs: ReplayResponse(text=payload),
    )

    assert market_data.get_us_stock_snapshots() == [
        {
            "symbol": "REPLAY",
            "name": "REPLAY",
            "price": 2.5,
            "pct": 12.3,
            "volume": 1_000_000.0,
            "dollar_volume": 2_500_000.0,
            "source": "polygon",
        }
    ]
