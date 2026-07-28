import json
from datetime import datetime, timedelta

import pytest

import main
from config import settings
from core.formatter import _infer_news_category
from core.analyzers.monitor import (
    _black_swan_alert_severity,
    _build_news_alert,
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


def test_funds_requires_dedicated_bot_credentials(monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    monkeypatch.setenv("TG_BOT_TOKEN_FUNDS", "funds-token")
    monkeypatch.setenv("TG_CHAT_ID_FUNDS", "funds-chat")

    main._validate_required_env("funds")


def test_daily_health_uses_monitor_credentials(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("TG_BOT_TOKEN_MONITOR", "token")
    monkeypatch.setenv("TG_CHAT_ID_MONITOR", "chat")

    main._validate_required_env("daily_health")


def test_daily_health_sends_recent_success_status(monkeypatch, tmp_path):
    import utils.notifier as notifier

    status_file = tmp_path / "runtime_status.json"
    status_file.write_text(
        json.dumps(
            {
                "mode": "monitor",
                "finished_at": datetime.now(settings.SHA_TZ).isoformat(),
                "status": "success",
                "data_fetch_success": True,
                "news_count": 10,
                "rss_count": 1,
                "telegram_attempted": False,
                "telegram_sent": False,
                "reason": "",
            }
        ),
        encoding="utf-8",
    )
    sent_messages = []
    monkeypatch.setattr(settings, "RUN_STATUS_FILE", str(status_file))
    monkeypatch.setattr(settings, "TG_BOT_TOKEN_MONITOR", "token")
    monkeypatch.setattr(settings, "TG_CHAT_ID_MONITOR", "chat")
    monkeypatch.setattr(
        notifier,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    main._send_daily_health_reminder()

    assert len(sent_messages) == 1
    assert "🟢 VPS 每日健康提醒：正常" in sent_messages[0][0]
    assert sent_messages[0][1] == {"token": "token", "chat_id": "chat"}


def test_daily_health_reports_stale_status_as_failure(monkeypatch, tmp_path):
    import utils.notifier as notifier

    status_file = tmp_path / "runtime_status.json"
    status_file.write_text(
        json.dumps(
            {
                "mode": "monitor",
                "finished_at": (
                    datetime.now(settings.SHA_TZ) - timedelta(minutes=31)
                ).isoformat(),
                "status": "success",
                "data_fetch_success": True,
                "news_count": 10,
                "rss_count": 1,
                "telegram_attempted": False,
                "telegram_sent": False,
                "reason": "",
            }
        ),
        encoding="utf-8",
    )
    sent_messages = []
    monkeypatch.setattr(settings, "RUN_STATUS_FILE", str(status_file))
    monkeypatch.setattr(settings, "TG_BOT_TOKEN_MONITOR", "token")
    monkeypatch.setattr(settings, "TG_CHAT_ID_MONITOR", "chat")
    monkeypatch.setattr(
        notifier,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    with pytest.raises(SystemExit):
        main._send_daily_health_reminder()

    assert len(sent_messages) == 1
    assert "🔴 VPS 每日健康提醒：需要检查" in sent_messages[0][0]


def test_runtime_summary_writes_configured_status_file(monkeypatch, tmp_path):
    import core.runtime as runtime

    status_file = tmp_path / "runtime_status.json"
    monkeypatch.setattr(settings, "RUN_STATUS_FILE", str(status_file))
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)
    runtime._start_run_summary("monitor")
    runtime._set_run_summary(data_fetch_success=True)
    runtime._print_run_summary()

    assert status_file.is_file()
    assert '"mode": "monitor"' in status_file.read_text(encoding="utf-8")


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
    trusted = {
        "source": "eastmoney",
        "link": "https://eastmoney.com/news/1",
        "importance": "high",
    }
    assert _is_black_swan_candidate(
        {"title": "突发军事冲突升级", "digest": "", **trusted}
    )
    assert _is_black_swan_candidate(
        {"title": "Global market circuit breaker", "digest": "", **trusted}
    )
    assert not _is_black_swan_candidate({"title": "公司发布季度业绩", "digest": ""})
    assert not _is_black_swan_candidate({"title": "行业政策支持出台", "digest": ""})


def test_monitor_rejects_generic_or_historical_black_swan_terms():
    trusted = {
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "importance": "high",
    }

    assert not _is_black_swan_candidate(
        {"title": "核能行业扩张计划", "digest": "", **trusted}
    )
    assert not _is_black_swan_candidate(
        {"title": "核设施扩建项目启动", "digest": "", **trusted}
    )
    assert not _is_black_swan_candidate(
        {"title": "Anniversary of declared war", "digest": "", **trusted}
    )
    assert not _is_black_swan_candidate(
        {"title": "军事演习模拟导弹袭击", "digest": "", **trusted}
    )


def test_monitor_marks_trusted_unverified_event_for_verification():
    item = {
        "title": "网传某国宣布进入紧急状态",
        "digest": "",
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "importance": "high",
    }

    assert _black_swan_alert_severity(item) == "待核实"


def test_monitor_marks_untrusted_event_for_verification():
    item = {
        "title": "突发军事冲突升级",
        "digest": "",
        "source": "custom-feed",
        "link": "https://example.com/news/1",
        "importance": "high",
    }

    assert _black_swan_alert_severity(item) == "待核实"


def test_monitor_keeps_specific_cyber_event_from_trusted_source():
    item = {
        "title": "Payment system outage disrupts market settlement",
        "digest": "",
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "importance": "high",
    }

    assert _black_swan_alert_severity(item) == "紧急"


def test_urgent_news_alert_includes_structured_market_impact():
    item = {
        "title": "突发导弹袭击升级",
        "digest": "",
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "datetime": datetime.now(settings.SHA_TZ),
        "category": "overseas",
        "importance": "high",
        "market_scope": "全球",
        "related_sectors": ["军工", "资源"],
    }

    content = _build_news_alert(item, "紧急")

    assert "【确认度】" in content
    assert "【传导路径】" in content
    assert "【A股映射】" in content
    assert "【后续验证】" in content
    assert "石油石化" in content


def test_unverified_news_alert_explains_verification_without_sector_call():
    item = {
        "title": "网传某国宣布进入紧急状态",
        "digest": "",
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "datetime": datetime.now(settings.SHA_TZ),
        "category": "overseas",
        "importance": "high",
        "market_scope": "全球",
        "related_sectors": [],
    }

    content = _build_news_alert(item, "待核实")

    assert "风险线索" in content
    assert "【后续验证】" in content
    assert "【A股映射】" not in content


def test_important_monetary_policy_alert_has_specific_market_analysis():
    item = {
        "title": "央行宣布下调存款准备金率",
        "digest": "",
        "source": "eastmoney",
        "link": "https://example.com/news/important",
        "datetime": datetime.now(settings.SHA_TZ),
        "category": "policy",
        "importance": "high",
        "market_scope": "市场",
        "related_sectors": ["金融"],
    }

    content = _build_news_alert(item, "重要")

    assert "【确认度】" in content
    assert "【传导路径】" in content
    assert "【A股映射】" in content
    assert "【后续验证】" in content
    assert "资金面、无风险利率和融资成本" in content
    assert "金融、地产" in content


def test_important_macro_alert_distinguishes_growth_data_from_policy():
    item = {
        "title": "PMI 数据高于市场预期",
        "digest": "制造业订单改善",
        "source": "eastmoney",
        "link": "https://example.com/news/macro",
        "datetime": datetime.now(settings.SHA_TZ),
        "category": "macro",
        "importance": "high",
        "market_scope": "A股",
        "related_sectors": ["资源", "汽车"],
    }

    content = _build_news_alert(item, "重要")

    assert "盈利和风险偏好预期" in content
    assert "同比、环比、季调口径及预期差" in content
    assert "资源、汽车" in content


def test_important_company_alert_does_not_overstate_sector_signal():
    item = {
        "title": "某公司筹划重大资产重组并停牌",
        "digest": "等待进一步公告",
        "source": "eastmoney",
        "link": "https://example.com/news/company",
        "datetime": datetime.now(settings.SHA_TZ),
        "category": "company",
        "importance": "high",
        "market_scope": "公司",
        "related_sectors": ["新能源"],
    }

    content = _build_news_alert(item, "重要")

    assert "重大公司事件" in content
    assert "不把单一公司的公告直接等同于行业趋势" in content
    assert "交易条款、审批条件、财务影响" in content


def test_funds_sends_result_to_dedicated_bot(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.funds as funds
    import core.runtime as runtime

    sent_messages = []
    monkeypatch.setattr(settings, "TG_BOT_TOKEN_FUNDS", "funds-token")
    monkeypatch.setattr(settings, "TG_CHAT_ID_FUNDS", "funds-chat")
    monkeypatch.setattr(
        funds,
        "get_market_funds",
        lambda: (
            [{"name": "半导体", "flow": 12.3, "change": "1.2%"}],
            [{"name": "地产", "flow": -8.1, "change": "-0.8%"}],
        ),
    )
    monkeypatch.setattr(funds, "get_news", lambda minutes: [])
    monkeypatch.setattr(
        analyzer, "_get_ai_response_with_health", lambda *args, **kwargs: "资金摘要"
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    funds.run_funds({})

    assert len(sent_messages) == 1
    assert "主力资金雷达" in sent_messages[0][0]
    assert sent_messages[0][1] == {
        "token": "funds-token",
        "chat_id": "funds-chat",
    }


def test_funds_sends_empty_data_health_status_to_dedicated_bot(monkeypatch):
    import core.analyzers.funds as funds
    import core.runtime as runtime

    sent_messages = []
    monkeypatch.setattr(settings, "TG_BOT_TOKEN_FUNDS", "funds-token")
    monkeypatch.setattr(settings, "TG_CHAT_ID_FUNDS", "funds-chat")
    monkeypatch.setattr(funds, "get_market_funds", lambda: ([], []))
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    funds.run_funds({})

    assert len(sent_messages) == 1
    assert "资金流数据为空" in sent_messages[0][0]
    assert sent_messages[0][1] == {
        "token": "funds-token",
        "chat_id": "funds-chat",
    }


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
    assert _news_alert_severity(
        {
            "title": "突发军事冲突升级",
            "digest": "",
            "source": "eastmoney",
            "link": "https://eastmoney.com/news/1",
            "importance": "high",
        }
    ) == "紧急"


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


def test_monitor_sends_unsent_news_after_per_cycle_limit(monkeypatch, tmp_path):
    import core.analyzers.monitor as monitor
    import core.runtime as runtime

    now = datetime.now(settings.SHA_TZ)
    items = [
        {
            "title": f"国务院发布资本市场新政策 {index}",
            "digest": "测试",
            "source": "eastmoney",
            "link": f"https://example.com/news/{index}",
            "datetime": now,
            "category": "policy",
            "importance": "high",
            "market_scope": "市场",
            "related_sectors": ["金融"],
        }
        for index in range(4)
    ]
    sent_messages = []

    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(tmp_path / "monitor.db"))
    monkeypatch.setattr(settings, "WATCHLIST_CODES", [])
    monkeypatch.setattr(monitor, "get_news", lambda *args, **kwargs: items)
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append(content) or True,
    )
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)

    monitor.run_monitor({})
    assert len(sent_messages) == 3
    monitor.run_monitor({})

    assert len(sent_messages) == 4


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
    import utils.notifier as notifier

    monkeypatch.setattr(settings, "TG_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "TG_CHAT_ID", None)
    monkeypatch.setattr(notifier, "_is_ci", lambda: False)

    assert notifier.send_tg("hello", token="", chat_id="") is False


def test_send_tg_raises_in_ci_when_credentials_missing(monkeypatch):
    from config import settings
    import utils.notifier as notifier

    monkeypatch.setattr(settings, "TG_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "TG_CHAT_ID", None)
    monkeypatch.setattr(notifier, "_is_ci", lambda: True)

    with pytest.raises(RuntimeError):
        notifier.send_tg("hello", token="", chat_id="")


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


def test_forward_returns_use_fixed_trading_sessions():
    from core.analyzers.review import _calculate_forward_returns

    closes = [{"close": 101 + idx} for idx in range(20)]
    returns = _calculate_forward_returns("100", closes)

    assert returns[1] == pytest.approx(1.0)
    assert returns[5] == pytest.approx(5.0)
    assert returns[20] == pytest.approx(20.0)


def test_local_language_detection_skips_translation_ai(monkeypatch):
    import core.data_fetcher as data_fetcher

    monkeypatch.setattr(
        data_fetcher,
        "get_ai_response",
        lambda *_args, **_kwargs: pytest.fail("Chinese news should not call AI"),
    )
    items = [{"title": "中国市场新闻", "digest": "测试内容"}]

    assert data_fetcher._normalize_external_news(items) == items


def test_semantic_dedup_skips_ai_for_dissimilar_titles(monkeypatch):
    import core.data_fetcher as data_fetcher

    monkeypatch.setattr(
        data_fetcher,
        "get_ai_response",
        lambda *_args, **_kwargs: pytest.fail("Dissimilar titles should not call AI"),
    )
    items = [
        {"title": "央行发布新的货币政策操作说明"},
        {"title": "某汽车公司公布新车型交付数据"},
    ]

    assert data_fetcher._deduplicate_semantic_news(items) == items
