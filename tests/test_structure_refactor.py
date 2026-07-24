import pytest

import main
from core.formatter import _infer_news_category
from core.analyzers.monitor import (
    _is_low_value_company_news,
    _is_monitor_alert_importance,
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


def test_send_tg_prefixes_split_chunks(monkeypatch):
    from config import settings
    from utils import notifier

    posted_payloads = []

    class DummyResponse:
        status_code = 200
        text = "ok"

    def fake_post(url, json, timeout):
        posted_payloads.append(json)
        return DummyResponse()

    monkeypatch.setattr(settings, "TG_BOT_TOKEN", "token")
    monkeypatch.setattr(settings, "TG_CHAT_ID", "chat")
    monkeypatch.setattr(notifier.requests, "post", fake_post)

    assert notifier.send_tg("A" * 4000) is True
    assert posted_payloads[0]["text"].startswith("[1/2]\n")
    assert posted_payloads[1]["text"].startswith("[2/2]\n")


def test_health_status_can_skip_telegram_notification(monkeypatch):
    import core.runtime as runtime

    def fail_send_tg(*args, **kwargs):
        raise AssertionError("send_tg should not be called")

    monkeypatch.setattr(runtime, "send_tg", fail_send_tg)
    runtime._start_run_summary("monitor")

    runtime._send_health_status("未发现重要信息", notify=False, severity="info")

    summary = runtime._get_run_summary()
    assert summary["status"] == "info"
    assert summary["telegram_attempted"] is False


def test_should_retry_ai_error_only_for_transient_statuses():
    import httpx
    from openai import APIConnectionError, APIStatusError
    from utils.ai_client import _should_retry_ai_error

    request = httpx.Request("POST", "https://api.deepseek.com")
    rate_limited = APIStatusError(
        "rate limited",
        response=httpx.Response(429, request=request),
        body=None,
    )
    bad_request = APIStatusError(
        "bad request",
        response=httpx.Response(400, request=request),
        body=None,
    )

    assert _should_retry_ai_error(APIConnectionError(request=request)) is True
    assert _should_retry_ai_error(rate_limited) is True
    assert _should_retry_ai_error(bad_request) is False
    assert _should_retry_ai_error(ValueError("bad")) is False


def test_run_recommend_success_without_network(monkeypatch, tmp_path):
    import core.analyzer as analyzer
    import core.runtime as runtime
    import core.analyzers.recommend as recommend_module
    from config import settings

    sent_messages = []
    pick_file = tmp_path / "stock_pick.json"
    history_file = tmp_path / "history.csv"

    monkeypatch.setattr(settings, "PICK_FILE", str(pick_file))
    monkeypatch.setattr(settings, "HISTORY_FILE", str(history_file))
    monkeypatch.setattr(
        recommend_module,
        "get_hot_stocks_data",
        lambda: [{"name": "真实候选", "code": "000001", "pct": "1%", "amount": "10亿"}],
    )
    monkeypatch.setattr(
        recommend_module,
        "get_news",
        lambda minutes: [
            {
                "title": "测试新闻",
                "digest": "测试摘要",
                "source": "test",
                "time_str": "09:30",
                "category": "industry",
                "importance": "medium",
                "market_scope": "行业",
                "related_sectors": [],
            }
        ],
    )
    monkeypatch.setattr(
        recommend_module,
        "get_stock_quote",
        lambda code: {"name": "真实候选", "price": "10.00", "pct": "1.00"},
    )
    monkeypatch.setattr(
        analyzer,
        "get_ai_response",
        lambda *args, **kwargs: (
            '{"name":"真实候选","code":"000001","reason":"测试理由"}'
        ),
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append(content) or True,
    )
    monkeypatch.setattr(runtime, "RUN_SUMMARY_FILE", str(tmp_path / "run_summary.txt"))

    analyzer.run_recommend()

    assert pick_file.exists()
    assert history_file.exists()
    assert sent_messages
    assert "真实候选" in sent_messages[0]
