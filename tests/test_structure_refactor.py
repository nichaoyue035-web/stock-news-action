import pytest

import main
from core.formatter import _infer_news_category
from core.analyzers.monitor import (
    MONITOR_SEEN_TTL,
    _filter_unseen_monitor_news,
    _is_black_swan_candidate,
    _is_low_value_company_news,
    _is_monitor_alert_importance,
    _load_recent_monitor_alerts,
    _parse_monitor_alert_line,
    _record_monitor_alerts,
)
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


def test_monitor_rejects_untrusted_or_contextual_black_swan_mentions():
    assert not _is_black_swan_candidate(
        {"title": "某自媒体称可能开战", "digest": "", "source": "unknown"}
    )
    assert not _is_black_swan_candidate(
        {
            "title": "历史回顾中的战争与市场",
            "digest": "",
            "source": "eastmoney",
            "importance": "high",
        }
    )


def test_monitor_alert_protocol_is_one_based_and_preserves_pipes():
    assert _parse_monitor_alert_line("ALERT|1|市场冲击|需核实", 2) == (
        0,
        "市场冲击|需核实",
    )
    assert _parse_monitor_alert_line("ALERT|0|错误编号", 2) is None
    assert _parse_monitor_alert_line("ALERT|3|越界", 2) is None


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


def test_monitor_seen_state_prevents_repeated_alerts(monkeypatch, tmp_path):
    from datetime import datetime
    from config import settings

    monkeypatch.setattr(settings, "MONITOR_STATE_FILE", str(tmp_path / "seen.json"))
    now = datetime.now(settings.SHA_TZ)
    item = {"title": "重要市场消息"}

    _record_monitor_alerts({}, [item], now)
    recent_alerts = _load_recent_monitor_alerts(now)

    assert _filter_unseen_monitor_news([item], recent_alerts) == []
    assert _filter_unseen_monitor_news(
        [{"title": "另一条重要消息"}], recent_alerts
    ) == [{"title": "另一条重要消息"}]


def test_monitor_seen_state_expires_old_alerts(monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    from config import settings

    monkeypatch.setattr(settings, "MONITOR_STATE_FILE", str(tmp_path / "seen.json"))
    now = datetime.now(settings.SHA_TZ)
    item = {"title": "过期消息"}
    old_time = now - MONITOR_SEEN_TTL - timedelta(seconds=1)

    _record_monitor_alerts({}, [item], old_time)

    assert _load_recent_monitor_alerts(now) == {}


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
