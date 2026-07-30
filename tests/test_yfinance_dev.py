from __future__ import annotations

from datetime import datetime, timezone

from config import settings
from core.yfinance_dev import (
    YFINANCE_BROAD_SCAN_RESULT_CAP,
    fetch_yfinance_broad_market_candidates,
    fetch_yfinance_dev_quotes,
    fetch_yfinance_event_evidence,
)


class _Ticker:
    def __init__(self, fast_info):
        self.fast_info = fast_info


def test_yfinance_development_probe_normalizes_and_screens_quotes(monkeypatch):
    monkeypatch.setattr(settings, "US_RADAR_MIN_PRICE", 1.0)
    monkeypatch.setattr(settings, "US_RADAR_MAX_PRICE", 5.0)
    monkeypatch.setattr(settings, "US_RADAR_MIN_DAY_CHANGE_PCT", 10.0)
    monkeypatch.setattr(settings, "US_RADAR_MAX_DAY_CHANGE_PCT", 30.0)
    monkeypatch.setattr(settings, "US_RADAR_MIN_DOLLAR_VOLUME", 1_000_000.0)

    quotes = fetch_yfinance_dev_quotes(
        ["test", "TEST"],
        ticker_factory=lambda _: _Ticker(
            {
                "last_price": 2.5,
                "previous_close": 2.0,
                "last_volume": 1_000_000,
            }
        ),
    )

    assert quotes == [
        {
            "symbol": "TEST",
            "price": 2.5,
            "previous_close": 2.0,
            "pct": 25.0,
            "reported_volume": 1_000_000,
            "estimated_dollar_volume": 2_500_000.0,
            "matches_current_us_radar_filters": True,
            "source": "yfinance-development-only",
        }
    ]


def test_yfinance_development_probe_skips_invalid_or_unavailable_quotes():
    calls = []

    def ticker_factory(symbol):
        calls.append(symbol)
        return _Ticker({"lastPrice": None})

    quotes = fetch_yfinance_dev_quotes(
        ["bad symbol", "FAIL", "FAIL"], ticker_factory=ticker_factory
    )

    assert quotes == []
    assert calls == ["FAIL"]


def test_yfinance_broad_market_probe_filters_a_capped_screener_response(monkeypatch):
    monkeypatch.setattr(settings, "US_RADAR_MIN_PRICE", 1.0)
    monkeypatch.setattr(settings, "US_RADAR_MAX_PRICE", 5.0)
    monkeypatch.setattr(settings, "US_RADAR_MIN_DAY_CHANGE_PCT", 10.0)
    monkeypatch.setattr(settings, "US_RADAR_MAX_DAY_CHANGE_PCT", 30.0)
    monkeypatch.setattr(settings, "US_RADAR_MIN_DOLLAR_VOLUME", 1_000_000.0)

    class Query:
        def __init__(self, *_):
            pass

    class YFinance:
        EquityQuery = Query

        @staticmethod
        def screen(query, **kwargs):
            assert isinstance(query, Query)
            assert kwargs == {
                "size": YFINANCE_BROAD_SCAN_RESULT_CAP,
                "sortField": "percentchange",
                "sortAsc": True,
            }
            return {
                "total": 300,
                "quotes": [
                    {
                        "symbol": "TEST",
                        "shortName": "测试股",
                        "regularMarketPrice": 2.5,
                        "regularMarketChangePercent": 25.0,
                        "regularMarketVolume": 1_000_000,
                    },
                    {
                        "symbol": "NOPE",
                        "regularMarketPrice": 8.0,
                        "regularMarketChangePercent": 30.0,
                        "regularMarketVolume": 1_000_000,
                    },
                ],
            }

    result = fetch_yfinance_broad_market_candidates(YFinance)

    assert result == {
        "provider_reported_total": 300,
        "result_cap": YFINANCE_BROAD_SCAN_RESULT_CAP,
        "returned_count": 2,
        "candidates": [
            {
                "symbol": "TEST",
                "name": "测试股",
                "price": 2.5,
                "pct": 25.0,
                "volume": 1_000_000,
                "dollar_volume": 2_500_000.0,
                "source": "yfinance-experimental-screener",
            }
        ],
    }


def test_yfinance_event_evidence_keeps_recent_items_and_failed_fetches(monkeypatch):
    monkeypatch.setattr(settings, "YFINANCE_DEV_EVENT_MAX_CANDIDATES", 2)
    monkeypatch.setattr(settings, "YFINANCE_DEV_EVENT_ITEMS_PER_SYMBOL", 3)
    monkeypatch.setattr(settings, "YFINANCE_DEV_EVENT_MAX_AGE_HOURS", 24)

    class NewsTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_news(self, **kwargs):
            assert kwargs == {"count": 3, "tab": "news"}
            if self.symbol == "FAIL":
                raise RuntimeError("unavailable")
            return [
                {
                    "content": {
                        "title": "Recent catalyst",
                        "pubDate": "2026-07-29T10:00:00Z",
                        "provider": {"displayName": "Example News"},
                        "canonicalUrl": {"url": "https://example.com/recent"},
                    }
                },
                {
                    "content": {
                        "title": "Old story",
                        "pubDate": "2026-07-27T10:00:00Z",
                    }
                },
            ]

    result = fetch_yfinance_event_evidence(
        ["TEST", "FAIL", "SKIPPED"],
        ticker_factory=NewsTicker,
        now=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
    )

    assert result == {
        "purpose": "第二层：仅核验近期可追溯新闻，不判断新闻必然导致行情异动",
        "candidate_limit": 2,
        "selected_count": 2,
        "not_checked_count": 1,
        "event_max_age_hours": 24,
        "recent_event_found_count": 1,
        "no_recent_event_count": 0,
        "event_fetch_failed_count": 1,
        "records": [
            {
                "symbol": "TEST",
                "event_evidence_status": "recent_traceable_event_found",
                "recent_event_items": [
                    {
                        "title": "Recent catalyst",
                        "publisher": "Example News",
                        "published_at": "2026-07-29T10:00:00+00:00",
                        "url": "https://example.com/recent",
                    }
                ],
                "requires_secondary_confirmation": True,
            },
            {
                "symbol": "FAIL",
                "event_evidence_status": "event_fetch_failed",
                "recent_event_items": [],
                "requires_secondary_confirmation": True,
                "failure_reason": "RuntimeError",
            },
        ],
    }
