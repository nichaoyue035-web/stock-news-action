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
