import pytest
from datetime import datetime, timedelta

import main
from config import settings
from core.formatter import _infer_news_category
from core.analyzers.monitor import (
    _is_black_swan_candidate,
    _is_low_value_company_news,
    _is_monitor_alert_importance,
    _news_alert_severity,
    _normalise_watchlist_codes,
)
from core.monitor_store import MonitorStore, news_event_key
from utils.notifier import _split_message


def test_mode_resolve_default_and_arg():
    assert main._resolve_mode(["main.py"]) == "daily"
    assert main._resolve_mode(["main.py", "monitor"]) == "monitor"


def test_missing_env_should_exit(monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    with pytest.raises(SystemExit):
        main._validate_required_env("daily")


def test_monitor_does_not_require_deepseek_credentials(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("TG_BOT_TOKEN_MONITOR", "token")
    monkeypatch.setenv("TG_CHAT_ID_MONITOR", "chat")

    main._validate_required_env("monitor")


def test_telegram_long_message_split():
    content = "A" * 8000
    chunks = list(_split_message(content, max_length=3900))
    assert len(chunks) == 3
    assert all(len(chunk) <= 3900 for chunk in chunks)
    assert "".join(chunks) == content


def test_news_category_infer():
    item = {"title": "国务院发布新政策支持科技创新", "digest": ""}
    assert _infer_news_category(item) == "政策"


def test_monitor_filters_low_importance_company_news():
    item = {
        "title": "某小公司公告签订日常订单",
        "digest": "单一公司经营进展",
        "category": "company",
        "importance": "low",
        "market_scope": "公司",
    }
    assert _is_low_value_company_news(item) is True


def test_monitor_keeps_high_impact_company_news():
    item = {
        "title": "某公司筹划重大资产重组并停牌",
        "digest": "可能影响板块风险偏好",
        "category": "company",
        "importance": "low",
        "market_scope": "公司",
    }
    assert _is_low_value_company_news(item) is False


def test_monitor_only_allows_high_or_elevated_importance():
    assert _is_monitor_alert_importance({"importance": "high"}) is True
    assert _is_monitor_alert_importance({"importance": "偏高"}) is True
    assert _is_monitor_alert_importance({"importance": "medium"}) is False
    assert _is_monitor_alert_importance({"importance": "low"}) is False


def test_monitor_only_keeps_black_swan_candidates():
    assert _is_black_swan_candidate({"title": "突发军事冲突升级", "digest": ""})
    assert _is_black_swan_candidate({"title": "Global market circuit breaker", "digest": ""})
    assert not _is_black_swan_candidate({"title": "公司发布季度业绩", "digest": ""})
    assert not _is_black_swan_candidate({"title": "行业政策支持出台", "digest": ""})


def test_monitor_classifies_market_news_without_ai():
    policy_item = {
        "title": "国务院发布资本市场新政策",
        "digest": "",
        "category": "policy",
        "importance": "high",
        "market_scope": "市场",
    }
    ordinary_company_item = {
        "title": "公司发布季度业绩",
        "digest": "",
        "category": "company",
        "importance": "high",
        "market_scope": "公司",
    }

    assert _news_alert_severity(policy_item) == "重要"
    assert _news_alert_severity(ordinary_company_item) is None
    assert _news_alert_severity({"title": "突发军事冲突升级", "digest": ""}) == "紧急"


def test_normalise_watchlist_codes_keeps_valid_unique_codes():
    assert _normalise_watchlist_codes(["1", "000001", "600519", "bad", ""]) == [
        "000001",
        "600519",
    ]


def test_monitor_fast_fetch_skips_ai_preprocessing(monkeypatch):
    import core.data_fetcher as data_fetcher

    class FakeResponse:
        text = '{"LivesList": []}'

    monkeypatch.setattr(data_fetcher.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(data_fetcher, "_fetch_external_rss_news", lambda *_: [])
    monkeypatch.setattr(
        data_fetcher,
        "_normalize_external_news",
        lambda *_: pytest.fail("external translation should be skipped"),
    )
    monkeypatch.setattr(
        data_fetcher,
        "_deduplicate_semantic_news",
        lambda *_: pytest.fail("semantic deduplication should be skipped"),
    )

    assert data_fetcher.get_news(
        20, semantic_dedup=False, translate_external=False
    ) == []


def test_monitor_store_persists_news_and_alert_delivery(tmp_path):
    store = MonitorStore(str(tmp_path / "monitor.db"))
    store.initialize()
    now = datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)
    item = {
        "title": "重要市场消息",
        "digest": "测试",
        "source": "eastmoney",
        "link": "https://example.com/news/1",
        "datetime": now,
    }

    assert store.record_news_event(item, now) is True
    assert store.record_news_event(item, now) is False
    alert_key = f"news:{news_event_key(item)}"
    assert store.claim_alert(
        alert_key=alert_key,
        dedup_key=alert_key,
        alert_type="news",
        severity="重要",
        payload=item,
        now=now,
    )
    store.mark_alert_sent(alert_key, now)
    assert not store.claim_alert(
        alert_key=alert_key,
        dedup_key=alert_key,
        alert_type="news",
        severity="重要",
        payload=item,
        now=now + timedelta(minutes=1),
    )


def test_monitor_store_retries_failures_and_applies_price_cooldown(tmp_path):
    store = MonitorStore(str(tmp_path / "monitor.db"))
    store.initialize()
    now = datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)

    assert store.claim_alert(
        alert_key="price:000001:one",
        dedup_key="price:000001:up",
        alert_type="price_move",
        severity="high",
        payload={},
        now=now,
        cooldown_minutes=15,
    )
    store.mark_alert_failed("price:000001:one", now, "timeout")
    assert store.claim_alert(
        alert_key="price:000001:one",
        dedup_key="price:000001:up",
        alert_type="price_move",
        severity="high",
        payload={},
        now=now + timedelta(minutes=1),
        cooldown_minutes=15,
    )
    store.mark_alert_sent("price:000001:one", now + timedelta(minutes=1))
    assert not store.claim_alert(
        alert_key="price:000001:two",
        dedup_key="price:000001:up",
        alert_type="price_move",
        severity="high",
        payload={},
        now=now + timedelta(minutes=2),
        cooldown_minutes=15,
    )


def test_monitor_store_returns_only_recent_previous_quote(tmp_path):
    store = MonitorStore(str(tmp_path / "monitor.db"))
    store.initialize()
    now = datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)

    assert (
        store.record_quote(
            code="000001",
            name="测试股",
            price=10.0,
            pct=0.0,
            observed_at=now,
            max_gap_minutes=3,
        )
        is None
    )
    previous = store.record_quote(
        code="000001",
        name="测试股",
        price=10.2,
        pct=2.0,
        observed_at=now + timedelta(minutes=1),
        max_gap_minutes=3,
    )
    assert previous is not None
    assert previous["price"] == 10.0
    assert (
        store.record_quote(
            code="000001",
            name="测试股",
            price=10.3,
            pct=3.0,
            observed_at=now + timedelta(minutes=10),
            max_gap_minutes=3,
        )
        is None
    )


def test_monitor_store_prevents_overlapping_cycles(tmp_path):
    store = MonitorStore(str(tmp_path / "monitor.db"))
    store.initialize()
    now = datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)

    assert store.acquire_lock("monitor", now)
    assert not store.acquire_lock("monitor", now + timedelta(minutes=1))
    store.release_lock("monitor")
    assert store.acquire_lock("monitor", now + timedelta(minutes=1))


def test_monitor_sends_each_important_news_event_once(monkeypatch, tmp_path):
    import core.analyzers.monitor as monitor
    import core.runtime as runtime

    now = datetime.now(settings.SHA_TZ)
    item = {
        "title": "国务院发布资本市场新政策",
        "digest": "测试",
        "source": "eastmoney",
        "link": "https://example.com/news/important",
        "datetime": now,
        "category": "policy",
        "importance": "high",
        "market_scope": "市场",
        "related_sectors": ["金融"],
    }
    sent_messages = []

    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(tmp_path / "monitor.db"))
    monkeypatch.setattr(settings, "WATCHLIST_CODES", [])
    monkeypatch.setattr(monitor, "get_news", lambda *args, **kwargs: [item])
    monkeypatch.setattr(runtime, "send_tg", lambda content, **kwargs: sent_messages.append(content) or True)
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)

    monitor.run_monitor({})
    monitor.run_monitor({})

    assert len(sent_messages) == 1
    assert "重要市场提醒" in sent_messages[0]


def test_watchlist_monitor_alerts_once_then_respects_cooldown(monkeypatch, tmp_path):
    import core.analyzers.monitor as monitor
    import core.runtime as runtime

    store = MonitorStore(str(tmp_path / "monitor.db"))
    store.initialize()
    now = datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)
    quotes = iter(
        [
            {"name": "测试股", "price": "10.00", "pct": "0.00"},
            {"name": "测试股", "price": "10.20", "pct": "2.00"},
            {"name": "测试股", "price": "10.30", "pct": "3.00"},
        ]
    )
    sent_messages = []

    monkeypatch.setattr(settings, "WATCHLIST_CODES", ["000001"])
    monkeypatch.setattr(settings, "PRICE_ALERT_MINUTE_CHANGE_PCT", 1.0)
    monkeypatch.setattr(settings, "PRICE_ALERT_COOLDOWN_MINUTES", 15)
    monkeypatch.setattr(monitor, "get_stock_quote", lambda code: next(quotes))
    monkeypatch.setattr(runtime, "send_tg", lambda content, **kwargs: sent_messages.append(content) or True)
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)

    assert monitor._run_watchlist_monitor(store, now) == (1, 0)
    assert monitor._run_watchlist_monitor(store, now + timedelta(minutes=1)) == (1, 1)
    assert monitor._run_watchlist_monitor(store, now + timedelta(minutes=2)) == (1, 0)
    assert len(sent_messages) == 1
    assert "自选股分钟异动" in sent_messages[0]


def test_validate_pick_rejects_candidate_not_in_list():
    from core.analyzer import _validate_pick_in_candidates

    pick = {"name": "不存在", "code": "999999", "reason": "测试"}
    candidates = [{"name": "测试股份", "code": "600000"}]

    assert _validate_pick_in_candidates(pick, candidates) is None


def test_validate_pick_normalizes_candidate_name_and_code():
    from core.analyzer import _validate_pick_in_candidates

    pick = {"name": "模型乱写名", "code": "1", "reason": "测试"}
    candidates = [{"name": "真实候选", "code": "000001"}]

    validated = _validate_pick_in_candidates(pick, candidates)

    assert validated == {"name": "真实候选", "code": "000001", "reason": "测试"}


def test_send_tg_returns_false_when_missing_credentials(monkeypatch):
    from config import settings
    from utils.notifier import send_tg

    monkeypatch.setattr(settings, "TG_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "TG_CHAT_ID", None)

    assert send_tg("hello", token="", chat_id="") is False


def test_append_history_reports_write_failure(monkeypatch, tmp_path):
    from config import settings
    from core.history import _append_history

    blocked_dir = tmp_path / "missing" / "history.csv"
    monkeypatch.setattr(settings, "HISTORY_FILE", str(blocked_dir))

    assert (
        _append_history({"name": "测试", "code": "000001", "reason": "测试"}, "1.23")
        is False
    )


def test_extract_pick_data_reads_first_json_object():
    from core.analyzer import _extract_pick_data

    content = (
        '说明文字 {"name":"测试","code":"000001","reason":"理由"} trailing {"x":1}'
    )

    assert _extract_pick_data(content) == {
        "name": "测试",
        "code": "000001",
        "reason": "理由",
    }


def test_send_health_status_attempts_telegram(monkeypatch):
    import core.runtime as runtime

    sent_messages = []

    def fake_send_tg(content, **kwargs):
        sent_messages.append((content, kwargs))
        return True

    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)
    monkeypatch.setattr(runtime, "send_tg", fake_send_tg)
    runtime._start_run_summary("daily")

    runtime._send_health_status("新闻数据为空")

    summary = runtime._get_run_summary()
    assert sent_messages
    assert summary["telegram_attempted"] is True
    assert summary["telegram_sent"] is True
    assert summary["status"] == "failed"


def test_redact_sensitive_text_redacts_configured_secrets(monkeypatch):
    from config import settings
    from utils.safety import redact_sensitive_text

    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "secret-key")

    assert redact_sensitive_text("failed with secret-key") == "failed with <redacted>"
