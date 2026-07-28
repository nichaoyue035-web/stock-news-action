from __future__ import annotations

from config import settings
from core.yfinance_dev import (
    YFINANCE_BROAD_SCAN_RESULT_CAP,
    fetch_yfinance_broad_market_candidates,
    fetch_yfinance_dev_quotes,
)


class _Ticker:
    def __init__(self, fast_info):
        self.fast_info = fast_info


def test_yfinance_development_probe_normalizes_and_screens_quotes(monkeypatch):
    monkeypatch.setattr(settings, "US_RADAR_MIN_PRICE", 1.0)
    monkeypatch.setattr(settings, "US_RADAR_MAX_PRICE", 5.0)
    monkeypatch.setattr(settings, "US_RADAR_MIN_DAY_CHANGE_PCT", 10.0)
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
                "sortAsc": False,
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
