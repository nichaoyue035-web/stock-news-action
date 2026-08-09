import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import main
from config import settings
from core.formatter import (
    _format_market_message,
    _infer_news_category,
    format_ai_insight,
)
from core.analyzers.monitor import (
    _black_swan_alert_severity,
    _build_news_alert,
    _is_black_swan_candidate,
    _is_low_value_company_news,
    _is_monitor_alert_importance,
    _news_alert_severity,
    _normalise_watchlist_codes,
    is_three_hour_market_summary_item,
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


def test_funds_uses_primary_bot_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    monkeypatch.setenv("TG_BOT_TOKEN", "primary-token")
    monkeypatch.setenv("TG_CHAT_ID", "primary-chat")

    main._validate_required_env("funds")


def test_daily_health_uses_monitor_credentials(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("TG_BOT_TOKEN_MONITOR", "token")
    monkeypatch.setenv("TG_CHAT_ID_MONITOR", "chat")

    main._validate_required_env("daily_health")


def test_telegram_listener_accepts_a_monitor_bot_without_a_primary_bot(monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    monkeypatch.setenv("TG_BOT_TOKEN_MONITOR", "token")
    monkeypatch.setenv("TG_CHAT_ID_MONITOR", "chat")

    main._validate_required_env("telegram_listener")


def test_radar_uses_primary_bot_credentials(monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN_MONITOR", raising=False)
    monkeypatch.delenv("TG_CHAT_ID_MONITOR", raising=False)
    monkeypatch.setenv("TG_BOT_TOKEN", "primary-token")
    monkeypatch.setenv("TG_CHAT_ID", "primary-chat")

    main._validate_required_env("radar")


def test_daily_health_sends_recent_success_status(monkeypatch, tmp_path):
    import core.runtime as runtime
    import utils.notifier as notifier

    status_dir = tmp_path / "runtime_status"
    monkeypatch.setattr(settings, "RUN_STATUS_DIR", str(status_dir))
    monkeypatch.setattr(settings, "HEALTH_REQUIRED_MODES", ("monitor",))
    status_file = Path(runtime.get_run_status_file("monitor"))
    status_file.parent.mkdir(parents=True)
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
    monkeypatch.setattr(settings, "TG_BOT_TOKEN_MONITOR", "token")
    monkeypatch.setattr(settings, "TG_CHAT_ID_MONITOR", "chat")
    monkeypatch.setattr(
        notifier,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    main._send_daily_health_reminder()

    assert len(sent_messages) == 1
    assert "🟢 系统正常" in sent_messages[0][0]
    assert sent_messages[0][1] == {"token": "token", "chat_id": "chat"}


def test_daily_health_reports_stale_status_as_failure(monkeypatch, tmp_path):
    import core.runtime as runtime
    import utils.notifier as notifier

    status_dir = tmp_path / "runtime_status"
    monkeypatch.setattr(settings, "RUN_STATUS_DIR", str(status_dir))
    monkeypatch.setattr(settings, "HEALTH_REQUIRED_MODES", ("monitor",))
    status_file = Path(runtime.get_run_status_file("monitor"))
    status_file.parent.mkdir(parents=True)
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
    assert "🔴 需要检查" in sent_messages[0][0]


def test_single_mode_health_rejects_partial_result(monkeypatch, tmp_path):
    import core.runtime as runtime

    monkeypatch.setattr(settings, "RUN_STATUS_DIR", str(tmp_path / "runtime_status"))
    status_file = Path(runtime.get_run_status_file("monitor"))
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "mode": "monitor",
                "finished_at": datetime.now(settings.SHA_TZ).isoformat(),
                "status": "partial",
                "reason": "海外 RSS 部分失败",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        main._print_health_status("monitor")


def test_runtime_summary_writes_configured_status_file(monkeypatch, tmp_path):
    import core.runtime as runtime

    status_file = tmp_path / "runtime_status.json"
    monkeypatch.setattr(settings, "RUN_STATUS_FILE", str(status_file))
    monkeypatch.setattr(settings, "RUN_STATUS_DIR", str(tmp_path / "runtime_status"))
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)
    runtime._start_run_summary("monitor")
    runtime._set_run_summary(data_fetch_success=True)
    runtime._print_run_summary()

    assert status_file.is_file()
    assert '"mode": "monitor"' in status_file.read_text(encoding="utf-8")
    assert Path(runtime.get_run_status_file("monitor")).is_file()


def test_mode_heartbeat_prevents_a_later_mode_from_hiding_a_failure(monkeypatch, tmp_path):
    import core.runtime as runtime

    monkeypatch.setattr(settings, "RUN_STATUS_FILE", str(tmp_path / "latest.json"))
    monkeypatch.setattr(settings, "RUN_STATUS_DIR", str(tmp_path / "runtime_status"))
    runtime._start_run_summary("daily")
    runtime._set_run_summary(data_fetch_success=True, status="success")
    runtime._print_run_summary()
    runtime._start_run_summary("monitor")
    runtime._set_run_summary(data_fetch_success=False, status="failed")
    runtime._print_run_summary()

    daily_status, _, _ = main._read_health_status("daily")
    monitor_status, _, _ = main._read_health_status("monitor")

    assert daily_status["status"] == "success"
    assert monitor_status["status"] == "failed"


def test_runtime_marks_empty_healthy_news_window_as_success(monkeypatch):
    import core.runtime as runtime
    from core.data_fetcher import record_data_source_health, reset_data_source_health

    reset_data_source_health()
    record_data_source_health("东方财富快讯", "success", "", 0)
    record_data_source_health("海外 RSS", "success", "", 0)
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)
    runtime._start_run_summary("monitor")

    runtime._record_news_summary([])

    assert runtime._get_run_summary()["data_fetch_success"] is True


def test_runtime_returns_nonzero_for_recorded_partial_result(monkeypatch, tmp_path):
    import core.runtime as runtime

    monkeypatch.setattr(settings, "RUN_STATUS_FILE", str(tmp_path / "latest.json"))
    monkeypatch.setattr(settings, "RUN_STATUS_DIR", str(tmp_path / "runtime_status"))

    @runtime._with_run_summary("daily")
    def incomplete_run():
        runtime._set_run_reason("可选数据源失败", status="partial")

    with pytest.raises(runtime.RunFailedError):
        incomplete_run()


def test_runtime_metrics_aggregate_modes_and_source_failures(monkeypatch, tmp_path):
    from core.metrics import (
        format_metrics,
        read_metrics,
        record_feedback_metric,
        record_run_metrics,
    )

    monkeypatch.setattr(settings, "METRICS_FILE", str(tmp_path / "metrics.json"))
    record_run_metrics(
        {
            "mode": "monitor",
            "status": "success",
            "finished_at": "2026-07-30T10:00:00+08:00",
            "duration_seconds": 1.2,
            "data_fetch_success": True,
            "telegram_attempted": False,
            "telegram_sent": False,
            "quality": {"input_items": 5, "alerts_sent": 1},
        },
        {"海外 RSS": {"status": "success", "count": 2}},
    )
    record_run_metrics(
        {
            "mode": "monitor",
            "status": "partial",
            "finished_at": "2026-07-30T10:05:00+08:00",
            "duration_seconds": 1.3,
            "data_fetch_success": False,
            "telegram_attempted": True,
            "telegram_sent": False,
        },
        {"海外 RSS": {"status": "failed", "count": 0}},
    )

    metrics = read_metrics()
    assert metrics["modes"]["monitor"]["runs"] == 2
    assert metrics["modes"]["monitor"]["partial"] == 1
    assert metrics["sources"]["海外 RSS"]["failed"] == 1
    assert "最近异常数据源：" in format_metrics("monitor")
    assert "输入 5" in format_metrics("monitor")

    record_feedback_metric("radar", "mute")
    record_feedback_metric("radar", "mute")
    record_feedback_metric("news", "continue_tracking")

    metrics = read_metrics()
    assert metrics["feedback"]["radar"]["mute"] == 2
    assert "用户反馈：" in format_metrics()


def test_failure_alert_uses_the_other_configured_telegram_channel(monkeypatch):
    import core.failure_notifier as failure_notifier

    sent = []
    monkeypatch.setattr(settings, "TG_BOT_TOKEN", "primary-token")
    monkeypatch.setattr(settings, "TG_CHAT_ID", "primary-chat")
    monkeypatch.setattr(settings, "TG_BOT_TOKEN_MONITOR", "monitor-token")
    monkeypatch.setattr(settings, "TG_CHAT_ID_MONITOR", "monitor-chat")
    monkeypatch.setattr(settings, "TELEGRAM_FAILURE_ALERTS_ENABLED", True)
    monkeypatch.setattr(
        failure_notifier,
        "send_tg",
        lambda content, **kwargs: sent.append((content, kwargs)) or True,
    )

    failure_notifier.send_failure_alert("stock-news@monitor.service")

    assert sent[0][1] == {"token": "primary-token", "chat_id": "primary-chat"}
    assert "stock-news@monitor.service" in sent[0][0]


def test_failure_alert_is_silent_by_default(monkeypatch):
    import core.failure_notifier as failure_notifier

    sent = []
    monkeypatch.setattr(settings, "TELEGRAM_FAILURE_ALERTS_ENABLED", False)
    monkeypatch.setattr(
        failure_notifier,
        "send_tg",
        lambda content, **kwargs: sent.append((content, kwargs)) or True,
    )

    failure_notifier.send_failure_alert("stock-news@monitor.service")

    assert sent == []


def test_data_fetcher_retries_transient_get_failure(monkeypatch):
    import requests
    import core.data_fetcher as data_fetcher

    class Response:
        status_code = 200

    attempts = []

    def fake_get(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.Timeout("temporary")
        return Response()

    monkeypatch.setattr(settings, "HTTP_GET_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "HTTP_GET_RETRY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)
    monkeypatch.setattr(data_fetcher.time, "sleep", lambda _seconds: None)

    assert data_fetcher._request_get("https://example.com").status_code == 200
    assert len(attempts) == 2


def test_telegram_long_message_split():
    content = "A" * 8000
    chunks = list(_split_message(content, max_length=3900))
    assert len(chunks) == 3
    assert all(len(chunk) <= 3900 for chunk in chunks)
    assert "".join(chunks) == content


def test_market_message_keeps_only_reader_relevant_context():
    content = _format_market_message(
        "盘中简报",
        report_time="2026-07-30 10:30",
        source="东方财富",
        category="market",
        importance="medium",
        market_scope="A股",
        related_sectors=["金融"],
        summary="【重点】流动性消息公布。",
        impact="【后续验证】核对午后成交。",
        links="https://example.com/news",
    )

    assert "分类" not in content
    assert "重要性" not in content
    assert "影响范围" not in content
    assert "重点：流动性消息公布。" in content
    assert "**解读**\n后续验证：核对午后成交。" in content


def test_ai_insight_formats_prediction_and_confidence_bar():
    content = format_ai_insight(
        "历史类比：政策预期变化通常先影响利率，再影响估值。\n"
        "预测：若细则落地，相关板块可能先出现分化。\n"
        "可信度：70%（来源和事实较完整）"
    )

    assert "**历史类比：**" in content
    assert "**预测：**" in content
    assert "**可信度：** ███████░░░ 70%" in content
    assert "未量化" in format_ai_insight("可信度：0-100%")


def test_telegram_preparation_preserves_bold_and_escapes_plain_angle_brackets():
    from utils.notifier import _prepare_content

    prepared = _prepare_content("**新闻标题**：A < 3；标签 <测试>")

    assert prepared == "<b>新闻标题</b>：A &lt; 3；标签 &lt;测试&gt;"


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


def test_monitor_trusts_first_batch_official_rss_sources():
    for host in ("ecb.europa.eu", "bis.org", "hkex.com", "sse.com.cn"):
        item = {
            "title": "Payment system outage disrupts market settlement",
            "digest": "",
            "source": host,
            "link": f"https://www.{host}/news/1",
            "importance": "high",
        }
        assert _black_swan_alert_severity(item) == "紧急"


def test_monitor_never_pushes_unverified_discovery_leads():
    item = {
        "title": "GDELT 线索｜major earthquake disrupts shipping",
        "digest": "仅作线索，未核验原文。",
        "source": "GDELT 线索",
        "link": "https://example.com/news/1",
        "importance": "high",
        "discovery_only": True,
    }

    assert _news_alert_severity(item) is None


def test_monitor_keeps_specific_cyber_event_from_trusted_source():
    item = {
        "title": "Payment system outage disrupts market settlement",
        "digest": "",
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "importance": "high",
    }

    assert _black_swan_alert_severity(item) == "紧急"


def test_urgent_news_alert_is_compact_and_keeps_the_market_essence():
    item = {
        "title": "银行挤兑引发流动性危机",
        "digest": "多家机构面临短期流动性压力。",
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "datetime": datetime.now(settings.SHA_TZ),
        "category": "macro",
        "importance": "high",
        "market_scope": "全球",
        "related_sectors": ["银行", "券商"],
    }

    content = _build_news_alert(item, "紧急")

    assert "🚨 紧急" in content
    assert "银行挤兑引发流动性危机" in content
    assert "**影响：** 关键在风险是否扩散为融资与信用压力。" in content
    assert "**历史相关：**" in content
    assert "**预测走势：**" in content
    assert "**可信度：**" in content
    assert "【确认度】" not in content
    assert "【分类】" not in content


def test_urgent_economic_alert_uses_ai_history_and_forecast():
    item = {
        "title": "市场熔断触发",
        "digest": "交易所暂停部分交易。",
        "source": "reuters",
        "link": "https://www.reuters.com/world/example",
        "datetime": datetime.now(settings.SHA_TZ),
        "category": "market_sentiment",
        "importance": "high",
        "market_scope": "全球",
        "related_sectors": ["金融"],
    }

    content = _build_news_alert(
        item,
        "紧急",
        ai_insight=(
            "历史类比：类似流动性冲击通常先影响风险偏好。\n"
            "预测：若交易恢复，压力可能缓解；否则风险继续扩散。\n"
            "验证点：核对恢复公告和信用利差。\n"
            "可信度：75%"
        ),
    )

    assert "**AI历史与预测：**" in content
    assert "**历史类比：** 类似流动性冲击通常先影响风险偏好。" in content
    assert "████████░░ 75%" in content


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
    assert "接着看：权威来源" in content
    assert "【" not in content


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

    assert "🔔 重要" in content
    assert "**影响：** 关键在资金与利率预期是否真正改变。" in content
    assert "**接着看：** 看正式工具、期限和规模，以及资金利率、收益率与金融地产反应。" in content
    assert "【重要性】" not in content


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

    assert "**重点：** 制造业订单改善" in content
    assert "**影响：** 关键是数据相对预期的变化，而不是单看绝对数。" in content
    assert "**接着看：** 看预期差、订单库存及资源、汽车与顺周期方向是否确认。" in content


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

    assert "**重点：** 等待进一步公告" in content
    assert "**影响：** 先看事项规模、审批条件和财务影响，不直接外推为行业趋势。" in content
    assert "**接着看：** 看正式公告及新能源、同业是否出现独立确认。" in content


def test_funds_sends_result_to_primary_bot(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.funds as funds
    import core.runtime as runtime

    sent_messages = []
    monkeypatch.setattr(settings, "TG_BOT_TOKEN", "primary-token")
    monkeypatch.setattr(settings, "TG_CHAT_ID", "primary-chat")
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
        analyzer,
        "_get_ai_response_with_health",
        lambda *args, **kwargs: (
            "【资金结论】资金信号需要持续性确认。\n"
            "【确认度】仅为单日数据。\n"
            "【传导路径】资金流向可能影响短期预期。\n"
            "【A股映射】观察半导体与地产。\n"
            "【后续验证】核对次日成交和价格。"
        ),
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    funds.run_funds({})

    assert len(sent_messages) == 1
    assert "主力资金雷达" in sent_messages[0][0]
    assert "资金温度：" in sent_messages[0][0]
    assert "流入且上涨（同向确认）" in sent_messages[0][0]
    assert "相关新闻：" in sent_messages[0][0]
    assert "**解读**\n资金结论：" in sent_messages[0][0]
    assert "**可信度：**" in sent_messages[0][0]
    assert sent_messages[0][1] == {}


def test_funds_sends_empty_data_health_status_to_primary_bot(monkeypatch):
    import core.analyzers.funds as funds
    import core.runtime as runtime

    sent_messages = []
    monkeypatch.setattr(settings, "TG_BOT_TOKEN", "primary-token")
    monkeypatch.setattr(settings, "TG_CHAT_ID", "primary-chat")
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
        "token": None,
        "chat_id": None,
    }


def test_periodic_separates_news_facts_from_structured_impact(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.periodic as periodic
    import core.runtime as runtime

    sent_messages = []
    news = [
        {
            "title": "央行发布最新流动性操作",
            "digest": "公开市场操作信息",
            "source": "eastmoney",
            "time_str": "10:30",
            "link": "https://example.com/periodic",
            "related_sectors": ["金融"],
        }
    ]
    monkeypatch.setattr(periodic, "get_news", lambda minutes: news)
    monkeypatch.setattr(
        analyzer,
        "_get_ai_response_with_health",
        lambda *args, **kwargs: (
            "【盘中主线】流动性预期仍待确认。\n"
            "【确认度】仅有单条公开信息。\n"
            "【传导路径】可能影响利率预期。\n"
            "【A股映射】观察金融。\n"
            "【后续验证】核对午后成交。"
        ),
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    periodic.run_periodic({})

    assert len(sent_messages) == 1
    assert "重点新闻：" in sent_messages[0][0]
    assert "**央行发布最新流动性操作**" in sent_messages[0][0]
    assert "**解读**\n盘中主线：" in sent_messages[0][0]


def test_daily_keeps_a_share_facts_when_ai_summary_is_unavailable(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.daily as daily
    import core.runtime as runtime

    sent_messages = []
    news = [
        {
            "title": "央行发布最新流动性操作",
            "digest": "公开市场操作信息",
            "source": "eastmoney",
            "time_str": "08:30",
            "link": "https://example.com/daily",
            "related_sectors": ["金融"],
        }
    ]
    monkeypatch.setattr(daily, "is_cn_a_share_trading_day", lambda _now: True)
    monkeypatch.setattr(daily, "get_news", lambda minutes: news)
    monkeypatch.setattr(
        analyzer,
        "_get_ai_response_with_health",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    daily.run_daily({})

    assert len(sent_messages) == 1
    assert "A股盘前简报（AI解读暂不可用）" in sent_messages[0][0]
    assert "**央行发布最新流动性操作**" in sent_messages[0][0]
    assert "AI解读：" in sent_messages[0][0]


def test_us_premarket_filters_a_share_only_news_and_sends_us_brief(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.us_market as us_market
    import core.runtime as runtime

    sent_messages = []
    prompts = []
    news = [
        {
            "title": "A股公司发布日常公告",
            "digest": "仅涉及A股公司",
            "source": "eastmoney",
            "time_str": "08:30",
            "link": "https://example.com/a-share",
            "market_scope": "A股",
            "category": "company",
            "related_sectors": ["金融"],
        },
        {
            "title": "SEC 披露｜NVDA｜8-K",
            "digest": "公司提交最新披露文件",
            "source": "SEC EDGAR",
            "time_str": "08:40",
            "link": "https://example.com/sec",
            "market_scope": "美股",
            "category": "company",
            "related_sectors": ["半导体"],
        },
    ]
    monkeypatch.setattr(us_market, "get_news", lambda minutes: news)
    monkeypatch.setattr(
        analyzer,
        "_get_ai_response_with_health",
        lambda prompt, **kwargs: prompts.append(prompt)
        or (
            "【隔夜焦点】SEC 披露已发布。\n"
            "【今日催化】暂未确认。\n"
            "【市场映射】关注半导体，需看后续文件。\n"
            "【开盘后验证】核对公告正文。"
        ),
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    us_market.run_us_premarket({})

    assert len(sent_messages) == 1
    assert "美股盘前简报" in sent_messages[0][0]
    assert "重点新闻：" in sent_messages[0][0]
    assert "**涉及：** 半导体" in sent_messages[0][0]
    assert "SEC 披露｜NVDA｜8-K" in prompts[0]
    assert "A股公司发布日常公告" not in prompts[0]


def test_us_midday_brief_skips_when_no_us_relevant_news(monkeypatch):
    import core.analyzers.us_market as us_market
    import core.runtime as runtime

    sent_messages = []
    monkeypatch.setattr(
        us_market,
        "get_news",
        lambda minutes: [
            {
                "title": "A股公司发布日常公告",
                "digest": "仅涉及A股公司",
                "source": "eastmoney",
                "time_str": "13:30",
                "link": "https://example.com/a-share",
                "market_scope": "A股",
                "category": "company",
                "related_sectors": [],
            }
        ],
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    us_market.run_us_periodic({})

    assert sent_messages == []


def test_after_market_separates_news_facts_from_structured_impact(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.after_market as after_market
    import core.runtime as runtime

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 28, 15, 20, tzinfo=tz)

    sent_messages = []
    news = [
        {
            "title": "半导体行业发布供需数据",
            "digest": "行业库存变化",
            "source": "eastmoney",
            "time_str": "15:00",
            "link": "https://example.com/after-market",
            "related_sectors": ["半导体"],
        }
    ]
    monkeypatch.setattr(after_market, "datetime", FixedDatetime)
    monkeypatch.setattr(after_market, "get_news", lambda minutes: news)
    monkeypatch.setattr(
        analyzer,
        "_get_ai_response_with_health",
        lambda *args, **kwargs: (
            "【收盘结论】行业信息仍需后续数据确认。\n"
            "【确认度】当前仅有单一来源。\n"
            "【传导路径】可能影响库存预期。\n"
            "【A股映射】观察半导体。\n"
            "【后续验证】核对订单和价格。"
        ),
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append((content, kwargs)) or True,
    )

    after_market.run_after_market({})

    assert len(sent_messages) == 1
    assert "重点新闻：" in sent_messages[0][0]
    assert "**半导体行业发布供需数据**" in sent_messages[0][0]
    assert "**解读**\n收盘结论：" in sent_messages[0][0]


def test_monitor_defers_important_market_news_to_three_hour_summary():
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

    assert _news_alert_severity(policy_item) is None
    assert is_three_hour_market_summary_item(policy_item)
    assert _news_alert_severity(ordinary_company_item) is None
    assert not is_three_hour_market_summary_item(ordinary_company_item)
    assert _news_alert_severity(
        {
            "title": "突发军事冲突升级",
            "digest": "",
            "source": "eastmoney",
            "link": "https://eastmoney.com/news/1",
            "importance": "high",
        }
    ) is None


def test_three_hour_summary_includes_important_domestic_market_news(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.global_macro as global_macro
    import core.runtime as runtime

    item = {
        "title": "国务院发布资本市场新政策",
        "digest": "政策文件明确优化长期资金入市安排。",
        "source": "eastmoney",
        "link": "https://example.com/policy",
        "category": "policy",
        "importance": "high",
        "market_scope": "市场",
        "related_sectors": ["金融"],
    }
    sent_messages = []
    prompts = []

    monkeypatch.setattr(global_macro, "get_news", lambda minutes: [item])
    monkeypatch.setattr(
        analyzer,
        "_get_ai_response_with_health",
        lambda prompt: prompts.append(prompt)
        or "【事件】资本市场政策调整\n【关键事实】已发布正式文件。\n【市场含义】关注资金预期变化。\n【后续验证】核对实施细则。",
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append(content) or True,
    )

    global_macro.run_global({})

    assert len(sent_messages) == 1
    assert "三小时市场总结" in sent_messages[0]
    assert "国务院发布资本市场新政策" in prompts[0]


def test_three_hour_summary_is_silent_when_no_news_passes_threshold(monkeypatch):
    import core.analyzer as analyzer
    import core.analyzers.global_macro as global_macro
    import core.runtime as runtime

    item = {
        "title": "公司发布季度业绩",
        "digest": "常规经营信息。",
        "source": "eastmoney",
        "link": "https://example.com/company",
        "category": "company",
        "importance": "high",
        "market_scope": "公司",
    }
    sent_messages = []

    monkeypatch.setattr(global_macro, "get_news", lambda minutes: [item])
    monkeypatch.setattr(
        analyzer,
        "_get_ai_response_with_health",
        lambda *args, **kwargs: pytest.fail("不应为普通公司新闻调用 AI"),
    )
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append(content) or True,
    )

    global_macro.run_global({})

    assert sent_messages == []


def test_normalise_watchlist_codes_keeps_valid_unique_codes():
    assert _normalise_watchlist_codes(["1", "000001", "600519", "bad", ""]) == [
        "000001",
        "600519",
    ]


def test_radar_scans_a_share_hot_pool_as_low_price_lead(monkeypatch, tmp_path):
    import core.radar as radar
    from core.radar_store import RadarStore

    store = RadarStore(str(tmp_path / "radar.db"))
    store.initialize()
    now = datetime(2026, 7, 30, 10, 0, tzinfo=settings.SHA_TZ)
    created = []

    monkeypatch.setattr(settings, "RADAR_A_SHARE_HOT_POOL_ENABLED", True)
    monkeypatch.setattr(settings, "RADAR_A_SHARE_HOT_POOL_MAX_NEW_CANDIDATES", 1)
    monkeypatch.setattr(
        radar,
        "get_hot_stocks_data",
        lambda: [
            {"code": "000001", "name": "测试股", "price": "8.5", "pct": "6.2%"},
            {"code": "000002", "name": "第二只", "price": "9.5", "pct": "8.0%"},
        ],
    )
    monkeypatch.setattr(
        radar,
        "_create_candidate",
        lambda _store, **kwargs: created.append(kwargs) or True,
    )

    sampled, candidates = radar._scan_a_share_hot_pool(store, now)

    assert sampled == 2
    assert candidates == 1
    assert created[0]["symbol"] == "000001"
    assert created[0]["attributes"]["source"] == "eastmoney-hot-pool"


def test_radar_scans_yahoo_experimental_pool_on_interval(monkeypatch, tmp_path):
    import core.radar as radar
    from core.radar_store import RadarStore

    store = RadarStore(str(tmp_path / "radar.db"))
    store.initialize()
    now = datetime(2026, 7, 30, 22, 10, tzinfo=settings.SHA_TZ)
    created = []

    monkeypatch.setattr(settings, "YFINANCE_EXPERIMENTAL_RADAR_ENABLED", True)
    monkeypatch.setattr(settings, "YFINANCE_EXPERIMENTAL_RADAR_INTERVAL_MINUTES", 10)
    monkeypatch.setattr(settings, "YFINANCE_EXPERIMENTAL_RADAR_MAX_NEW_CANDIDATES", 1)
    monkeypatch.setattr(
        radar,
        "fetch_yfinance_broad_market_candidates",
        lambda: {
            "returned_count": 12,
            "candidates": [
                {
                    "symbol": "TEST",
                    "name": "Test Corp",
                    "price": 2.5,
                    "pct": 12.0,
                    "volume": 1_000_000,
                    "dollar_volume": 2_500_000,
                }
            ],
        },
    )
    monkeypatch.setattr(
        radar,
        "_create_candidate",
        lambda _store, **kwargs: created.append(kwargs) or True,
    )

    sampled, candidates = radar._scan_yahoo_experimental_candidates(store, now)

    assert sampled == 12
    assert candidates == 1
    assert created[0]["market"] == "US"
    assert created[0]["attributes"]["source"] == "yfinance-experimental-screener"


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


def test_second_batch_sec_edgar_requires_explicit_configuration(monkeypatch):
    import core.data_fetcher as data_fetcher

    monkeypatch.setattr(data_fetcher.settings, "SEC_WATCHLIST_TICKERS", [])
    data_fetcher.reset_data_source_health()

    assert data_fetcher._fetch_sec_edgar_filings(60) == []
    assert data_fetcher.get_data_source_health()["SEC EDGAR"]["status"] == "skipped"


def test_second_batch_sec_edgar_normalizes_recent_filing(monkeypatch):
    import core.data_fetcher as data_fetcher

    accepted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, **_kwargs):
        if url == data_fetcher.SEC_TICKERS_URL:
            return Response(
                {"0": {"ticker": "TEST", "cik_str": 1234, "title": "Test Corp"}}
            )
        assert url == data_fetcher.SEC_SUBMISSIONS_URL.format(cik="0000001234")
        return Response(
            {
                "filings": {
                    "recent": {
                        "form": ["8-K"],
                        "acceptanceDateTime": [accepted_at],
                        "accessionNumber": ["0000001234-26-000001"],
                        "primaryDocument": ["form8k.htm"],
                        "reportDate": ["2026-07-30"],
                    }
                }
            }
        )

    monkeypatch.setattr(data_fetcher.settings, "SEC_WATCHLIST_TICKERS", ["TEST"])
    monkeypatch.setattr(data_fetcher.settings, "SEC_USER_AGENT", "Test Agent test@example.com")
    monkeypatch.setattr(data_fetcher.settings, "SEC_EDGAR_ALLOWED_FORMS", ("8-K",))
    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    items = data_fetcher._fetch_sec_edgar_filings(60)

    assert len(items) == 1
    assert items[0]["title"] == "SEC 披露｜TEST｜8-K"
    assert items[0]["market_scope"] == "美股"
    assert items[0]["link"].endswith("/000000123426000001/form8k.htm")


def test_second_batch_cn_official_page_keeps_only_material_dated_notice(monkeypatch):
    import core.data_fetcher as data_fetcher

    date_text = datetime.now(settings.SHA_TZ).strftime("%Y-%m-%d")

    class Response:
        text = (
            "<ul>"
            f"<li>{date_text}<a href='/material'>关于融资融券监管规则的公告</a></li>"
            f"<li>{date_text}<a href='/ordinary'>一般会议通知</a></li>"
            "</ul>"
        )

        def raise_for_status(self):
            return None

    monkeypatch.setattr(data_fetcher.requests, "get", lambda *_args, **_kwargs: Response())
    items = data_fetcher._fetch_cn_official_news(
        source_name="中国证监会",
        feed_url="https://www.csrc.gov.cn/",
        material_terms=data_fetcher.CSRC_MATERIAL_TERMS,
        minutes_lookback=1440,
    )

    assert len(items) == 1
    assert items[0]["title"] == "关于融资融券监管规则的公告"
    assert items[0]["link"] == "https://www.csrc.gov.cn/material"
    assert items[0]["published_time_precision"] == "date"


def test_second_batch_gdelt_is_discovery_only(monkeypatch):
    import core.data_fetcher as data_fetcher

    seen_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "articles": [
                    {
                        "title": "Major earthquake disrupts shipping",
                        "url": "https://example.com/article",
                        "domain": "example.com",
                        "seendate": seen_at,
                    }
                ]
            }

    monkeypatch.setattr(data_fetcher.settings, "GDELT_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(data_fetcher.settings, "GDELT_DISCOVERY_QUERY", "earthquake")
    monkeypatch.setattr(data_fetcher.settings, "GDELT_DISCOVERY_MAX_RECORDS", 5)
    monkeypatch.setattr(data_fetcher.requests, "get", lambda *_args, **_kwargs: Response())

    items = data_fetcher._fetch_gdelt_discovery_news(60)

    assert len(items) == 1
    assert items[0]["discovery_only"] is True
    assert _news_alert_severity(items[0]) is None


def test_trump_media_relay_requires_explicit_enablement(monkeypatch):
    import core.data_fetcher as data_fetcher

    monkeypatch.setattr(data_fetcher.settings, "TRUMP_MEDIA_RELAY_ENABLED", False)
    data_fetcher.reset_data_source_health()

    assert data_fetcher._fetch_trump_media_relay(60) == []
    assert (
        data_fetcher.get_data_source_health()["特朗普帖文媒体转述"]["status"]
        == "skipped"
    )


def test_trump_media_relay_keeps_only_trusted_recent_article(monkeypatch):
    import core.data_fetcher as data_fetcher

    seen_at = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    class Response:
        content = f"""
        <rss><channel>
          <item>
            <title>Trump posts a new Truth Social message</title>
            <link>https://news.google.com/rss/articles/reuters-item</link>
            <pubDate>{seen_at}</pubDate>
            <source url="https://www.reuters.com">Reuters</source>
          </item>
          <item>
            <title>Untrusted relay copy</title>
            <link>https://news.google.com/rss/articles/untrusted-item</link>
            <pubDate>{seen_at}</pubDate>
            <source url="https://example.com">Example</source>
          </item>
        </channel></rss>
        """.encode()

        def raise_for_status(self):
            return None

    monkeypatch.setattr(data_fetcher.settings, "TRUMP_MEDIA_RELAY_ENABLED", True)
    monkeypatch.setattr(data_fetcher.settings, "TRUMP_MEDIA_RELAY_MAX_RECORDS", 5)
    monkeypatch.setattr(data_fetcher.requests, "get", lambda *_args, **_kwargs: Response())

    items = data_fetcher._fetch_trump_media_relay(60)

    assert len(items) == 1
    assert items[0]["source"] == "特朗普帖文媒体转述｜Reuters"
    assert items[0]["link"] == "https://news.google.com/rss/articles/reuters-item"
    assert items[0]["media_relay"] is True


def test_truth_social_requires_explicit_enablement(monkeypatch):
    import core.data_fetcher as data_fetcher

    monkeypatch.setattr(data_fetcher.settings, "TRUTH_SOCIAL_ENABLED", False)
    data_fetcher.reset_data_source_health()

    assert data_fetcher._fetch_truth_social_posts(60) == []
    assert data_fetcher.get_data_source_health()["Truth Social（特朗普）"]["status"] == "skipped"


def test_truth_social_normalizes_recent_public_post(monkeypatch):
    import core.data_fetcher as data_fetcher

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "id": "123456789",
                    "created_at": created_at,
                    "content": "<p>Trade <strong>policy</strong> update</p>",
                    "url": "https://truthsocial.com/@realDonaldTrump/123456789",
                    "account": {"id": "107780257626128497"},
                }
            ]

    monkeypatch.setattr(data_fetcher.settings, "TRUTH_SOCIAL_ENABLED", True)
    monkeypatch.setattr(data_fetcher.requests, "get", lambda *_args, **_kwargs: Response())

    items = data_fetcher._fetch_truth_social_posts(60)

    assert len(items) == 1
    assert items[0]["title"] == "特朗普 Truth Social｜Trade policy update"
    assert items[0]["digest"].endswith("Trade policy update")
    assert items[0]["link"] == "https://truthsocial.com/@realDonaldTrump/123456789"
    assert items[0]["primary_source"] is True


def test_truth_social_rejects_non_json_response(monkeypatch):
    import core.data_fetcher as data_fetcher

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("anti-bot page")

    monkeypatch.setattr(data_fetcher.settings, "TRUTH_SOCIAL_ENABLED", True)
    monkeypatch.setattr(data_fetcher.requests, "get", lambda *_args, **_kwargs: Response())
    data_fetcher.reset_data_source_health()

    assert data_fetcher._fetch_truth_social_posts(60) == []
    assert data_fetcher.get_data_source_health()["Truth Social（特朗普）"]["status"] == "failed"


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


def test_news_tracking_callback_starts_and_stops_for_authorized_user(
    monkeypatch, tmp_path
):
    import core.analyzers.monitor as monitor

    database = tmp_path / "monitor.db"
    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(database))
    monkeypatch.setattr(settings, "METRICS_FILE", str(tmp_path / "metrics.json"))
    monkeypatch.setattr(settings, "INTERACTION_ALLOWED_USER_IDS", [999])
    now = datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)
    item = {
        "title": "NVIDIA export restriction escalates",
        "digest": "official update",
        "source": "official",
        "link": "https://example.com/original",
        "datetime": now,
    }
    store = MonitorStore(str(database))
    store.initialize()
    event_key = news_event_key(item)
    tracking_id = store.offer_news_tracking(
        event_key=event_key, item=item, telegram_chat_id="123", now=now
    )
    callback = {
        "data": f"news:{tracking_id}:120",
        "from": {"id": 999},
        "message": {"chat": {"id": 123, "type": "supergroup"}},
    }

    assert "已开启 2 小时" in monitor.handle_news_tracking_callback(callback, now)
    assert store.get_news_tracker(tracking_id)["status"] == "tracking"

    callback["data"] = f"news:{tracking_id}:stop"
    assert "已停止" in monitor.handle_news_tracking_callback(
        callback, now + timedelta(minutes=1)
    )
    assert store.get_news_tracker(tracking_id)["status"] == "closed"


def test_news_tracking_sends_only_new_related_source_items(monkeypatch, tmp_path):
    import core.analyzers.monitor as monitor
    import core.runtime as runtime

    database = tmp_path / "monitor.db"
    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(database))
    now = datetime(2026, 7, 28, 10, 0, tzinfo=settings.SHA_TZ)
    original = {
        "title": "NVIDIA export restriction escalates",
        "digest": "official update",
        "source": "official",
        "link": "https://example.com/original",
        "datetime": now,
    }
    related = {
        "title": "NVIDIA restriction details confirmed",
        "digest": "new source",
        "source": "second source",
        "link": "https://example.com/update",
        "datetime": now + timedelta(minutes=1),
    }
    unrelated = {
        "title": "Central bank policy update",
        "digest": "unrelated",
        "source": "third source",
        "link": "https://example.com/other",
        "datetime": now + timedelta(minutes=1),
    }
    store = MonitorStore(str(database))
    store.initialize()
    event_key = news_event_key(original)
    store.record_news_event(original, now)
    tracking_id = store.offer_news_tracking(
        event_key=event_key, item=original, telegram_chat_id="123", now=now
    )
    assert store.activate_news_tracker(tracking_id, 120, now)
    store.record_news_event(related, now + timedelta(minutes=1))
    store.record_news_event(unrelated, now + timedelta(minutes=1))
    sent_messages = []
    monkeypatch.setattr(
        runtime,
        "_send_tg_with_summary",
        lambda content, **kwargs: sent_messages.append(content) or True,
    )

    assert monitor._process_active_news_trackers(store, now + timedelta(minutes=1)) == (1, 0)
    assert len(sent_messages) == 1
    assert "NVIDIA restriction details confirmed" in sent_messages[0]
    assert store.get_news_tracker(tracking_id)["update_count"] == 1


def test_news_tracking_buttons_include_callback_controls_and_source_link():
    import core.analyzers.monitor as monitor

    markup = monitor._news_tracking_buttons("a" * 64, "https://example.com/source")
    assert markup["inline_keyboard"][0][0]["callback_data"] == "news:" + "a" * 16 + ":120"
    assert markup["inline_keyboard"][0][1]["callback_data"].endswith(":stop")
    assert markup["inline_keyboard"][1][0]["url"] == "https://example.com/source"


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
            max_gap_minutes=6,
        )
        is None
    )
    previous = store.record_quote(
        code="000001",
        name="测试股",
        price=10.2,
        pct=2.0,
        observed_at=now + timedelta(minutes=5),
        max_gap_minutes=6,
    )
    assert previous is not None
    assert previous["price"] == 10.0
    assert (
        store.record_quote(
            code="000001",
            name="测试股",
            price=10.3,
            pct=3.0,
            observed_at=now + timedelta(minutes=12),
            max_gap_minutes=6,
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


def test_maintenance_prunes_expired_state_and_creates_sqlite_backup(
    monkeypatch, tmp_path
):
    from core.maintenance import run_maintenance
    from core.radar_store import RadarStore

    database = tmp_path / "monitor.db"
    backup_dir = tmp_path / "backups"
    now = datetime.now(settings.SHA_TZ)
    old = now - timedelta(days=2)
    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(database))
    monkeypatch.setattr(settings, "STATE_BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(settings, "DB_RETENTION_DAYS", 1)
    monkeypatch.setattr(settings, "DB_BACKUP_RETENTION_DAYS", 7)
    monkeypatch.setattr(settings, "RUN_STATUS_FILE", str(tmp_path / "latest.json"))
    monkeypatch.setattr(settings, "RUN_STATUS_DIR", str(tmp_path / "runtime_status"))
    monkeypatch.setattr(settings, "METRICS_FILE", str(tmp_path / "metrics.json"))
    monkeypatch.setattr(settings, "OFFSITE_BACKUP_ENABLED", False)

    monitor_store = MonitorStore(str(database))
    radar_store = RadarStore(str(database))
    monitor_store.initialize()
    radar_store.initialize()
    monitor_store.record_news_event(
        {
            "title": "过期新闻",
            "digest": "测试",
            "source": "test",
            "link": "https://example.com/old",
            "datetime": old,
        },
        old,
    )
    monitor_store.record_quote(
        code="000001",
        name="测试股",
        price=10.0,
        pct=0.0,
        observed_at=old,
        max_gap_minutes=6,
    )
    radar_store.record_quote(
        market="CN",
        symbol="000001",
        name="测试股",
        price=10.0,
        pct=0.0,
        volume=None,
        observed_at=old,
    )

    run_maintenance()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM news_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM market_quotes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM radar_quotes").fetchone()[0] == 0
    assert list(backup_dir.glob("monitor-*.sqlite3"))
    assert list(backup_dir.glob("stock-news-state-*.zip"))


def test_enabled_offsite_backup_fails_when_rclone_upload_fails(monkeypatch, tmp_path):
    import core.maintenance as maintenance

    archive = tmp_path / "stock-news-state-test.zip"
    archive.write_bytes(b"backup")
    monkeypatch.setattr(settings, "OFFSITE_BACKUP_ENABLED", True)
    monkeypatch.setattr(
        settings, "OFFSITE_BACKUP_RCLONE_TARGET", "remote:stock-news-action"
    )
    monkeypatch.setattr(maintenance.shutil, "which", lambda _name: "/usr/bin/rclone")
    monkeypatch.setattr(
        maintenance.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 3})(),
    )

    with pytest.raises(maintenance.OffsiteBackupError, match="rclone exit 3"):
        maintenance._upload_offsite_backup(archive)


def test_enabled_offsite_backup_uses_timestamped_recovery_archive(monkeypatch, tmp_path):
    import core.maintenance as maintenance

    archive = tmp_path / "stock-news-state-test.zip"
    archive.write_bytes(b"backup")
    monkeypatch.setattr(settings, "OFFSITE_BACKUP_ENABLED", True)
    monkeypatch.setattr(
        settings, "OFFSITE_BACKUP_RCLONE_TARGET", "remote:stock-news-action"
    )
    monkeypatch.setattr(maintenance.shutil, "which", lambda _name: "/usr/bin/rclone")
    commands = []
    monkeypatch.setattr(
        maintenance.subprocess,
        "run",
        lambda args, **_kwargs: commands.append(args)
        or type("Result", (), {"returncode": 0})(),
    )

    destination = maintenance._upload_offsite_backup(archive)

    assert destination == "remote:stock-news-action/stock-news-state-test.zip"
    assert commands == [
        [
            "rclone",
            "copyto",
            str(archive),
            destination,
            "--retries",
            "2",
        ]
    ]


def test_monitor_defers_important_news_without_minute_level_delivery(monkeypatch, tmp_path):
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

    assert sent_messages == []


def test_monitor_deduplicates_cross_source_urgent_alerts(
    monkeypatch, tmp_path
):
    import core.analyzers.monitor as monitor
    import core.runtime as runtime

    now = datetime.now(settings.SHA_TZ)
    items = [
        {
            "title": "国务院发布资本市场新政策",
            "digest": "政策文件明确优化资本市场长期资金入市安排。",
            "source": "eastmoney",
            "link": "https://eastmoney.com/news/policy",
            "datetime": now,
            "category": "policy",
            "importance": "high",
            "market_scope": "市场",
            "related_sectors": ["金融"],
        },
        {
            "title": "国务院发布资本市场新政策",
            "digest": "政策文件明确优化资本市场长期资金入市安排。",
            "source": "reuters",
            "link": "https://reuters.com/news/policy",
            "datetime": now,
            "category": "policy",
            "importance": "high",
            "market_scope": "市场",
            "related_sectors": ["金融"],
        },
        {
            "title": "Payment system outage disrupts market settlement",
            "digest": "The outage interrupted market settlement services.",
            "source": "reuters",
            "link": "https://reuters.com/news/outage",
            "datetime": now,
            "category": "overseas",
            "importance": "high",
            "market_scope": "海外",
            "related_sectors": ["金融 IT"],
        },
        {
            "title": "Payment system outage disrupts market settlement",
            "digest": "The outage interrupted market settlement services.",
            "source": "bbc",
            "link": "https://bbc.com/news/outage",
            "datetime": now,
            "category": "overseas",
            "importance": "high",
            "market_scope": "海外",
            "related_sectors": ["金融 IT"],
        },
    ]
    sent_messages = []

    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(tmp_path / "monitor.db"))
    monkeypatch.setattr(settings, "MONITOR_MARKET_ALERT_DEDUP_MINUTES", 60)
    monkeypatch.setattr(settings, "WATCHLIST_CODES", [])
    monkeypatch.setattr(monitor, "get_news", lambda *args, **kwargs: items)
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append(content) or True,
    )
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)

    monitor.run_monitor({})

    assert len(sent_messages) == 1
    assert sum("🚨 紧急" in message for message in sent_messages) == 1


def test_monitor_defers_material_policy_updates_to_three_hour_summary(monkeypatch, tmp_path):
    import core.analyzers.monitor as monitor
    import core.runtime as runtime

    now = datetime.now(settings.SHA_TZ)
    items = [
        {
            "title": "央行宣布准备金率调整",
            "digest": "本次下调 0.25 个百分点。",
            "source": "eastmoney",
            "link": "https://eastmoney.com/news/reserve-one",
            "datetime": now,
            "category": "policy",
            "importance": "high",
            "market_scope": "市场",
            "related_sectors": ["金融"],
        },
        {
            "title": "央行宣布准备金率调整",
            "digest": "本次下调 0.50 个百分点。",
            "source": "reuters",
            "link": "https://reuters.com/news/reserve-two",
            "datetime": now,
            "category": "policy",
            "importance": "high",
            "market_scope": "市场",
            "related_sectors": ["金融"],
        },
    ]
    sent_messages = []

    monkeypatch.setattr(settings, "MONITOR_DB_FILE", str(tmp_path / "monitor.db"))
    monkeypatch.setattr(settings, "MONITOR_MARKET_ALERT_DEDUP_MINUTES", 60)
    monkeypatch.setattr(settings, "WATCHLIST_CODES", [])
    monkeypatch.setattr(monitor, "get_news", lambda *args, **kwargs: items)
    monkeypatch.setattr(
        runtime,
        "send_tg",
        lambda content, **kwargs: sent_messages.append(content) or True,
    )
    monkeypatch.setattr(runtime, "CURRENT_RUN_SUMMARY", None)

    monitor.run_monitor({})

    assert sent_messages == []


def test_monitor_sends_unsent_news_after_per_cycle_limit(monkeypatch, tmp_path):
    import core.analyzers.monitor as monitor
    import core.runtime as runtime

    now = datetime.now(settings.SHA_TZ)
    items = [
        {
            "title": f"Payment system outage disrupts market settlement {index}",
            "digest": "The outage interrupted market settlement services.",
            "source": "reuters",
            "link": f"https://example.com/news/{index}",
            "datetime": now,
            "category": "overseas",
            "importance": "high",
            "market_scope": "海外",
            "related_sectors": ["金融 IT"],
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


def test_send_tg_adds_a_compact_status_button(monkeypatch):
    import utils.notifier as notifier

    class Response:
        status_code = 200
        text = ""

    sent_payloads = []
    monkeypatch.setattr(
        notifier.requests,
        "post",
        lambda _url, **kwargs: sent_payloads.append(kwargs["json"]) or Response(),
    )

    assert notifier.send_tg("hello", token="token", chat_id="chat") is True
    assert sent_payloads[0]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "📊 状态", "callback_data": "system:status"}]
        ]
    }
    assert sent_payloads[0]["parse_mode"] == "HTML"


def test_interactive_message_keeps_its_actions_and_adds_status_button(monkeypatch):
    import utils.notifier as notifier

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"result": {"message_id": 123}}

    sent_payloads = []
    monkeypatch.setattr(
        notifier.requests,
        "post",
        lambda _url, **kwargs: sent_payloads.append(kwargs["json"]) or Response(),
    )
    original_markup = {
        "inline_keyboard": [[{"text": "继续跟踪", "callback_data": "radar:x:120"}]]
    }

    assert (
        notifier.send_tg_interactive(
            "hello", reply_markup=original_markup, token="token", chat_id="chat"
        )
        == 123
    )
    assert sent_payloads[0]["reply_markup"]["inline_keyboard"] == [
        [{"text": "继续跟踪", "callback_data": "radar:x:120"}],
        [{"text": "📊 状态", "callback_data": "system:status"}],
    ]
    assert original_markup["inline_keyboard"] == [
        [{"text": "继续跟踪", "callback_data": "radar:x:120"}]
    ]


def test_append_history_reports_write_failure(monkeypatch, tmp_path):
    from config import settings
    from core.history import _append_history

    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(settings, "HISTORY_FILE", str(blocked_parent / "history.csv"))

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


def test_news_source_failures_do_not_mislabel_eastmoney(monkeypatch):
    import core.data_fetcher as data_fetcher

    class FakeResponse:
        text = '{"LivesList": []}'

    data_fetcher.reset_data_source_health()
    monkeypatch.setattr(data_fetcher.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        data_fetcher,
        "_fetch_external_rss_news",
        lambda *_: (_ for _ in ()).throw(RuntimeError("rss unavailable")),
    )
    monkeypatch.setattr(data_fetcher, "_fetch_second_batch_news", lambda *_: [])

    assert data_fetcher.get_news(20, semantic_dedup=False, translate_external=False) == []
    health = data_fetcher.get_data_source_health()
    assert health["东方财富快讯"]["status"] == "success"
    assert health["海外 RSS"]["status"] == "failed"


def test_market_holiday_configuration_skips_scheduled_market_work(monkeypatch):
    from core.market_calendar import is_cn_a_share_trading_day, is_us_equity_trading_day

    moment = datetime(2026, 7, 28, 10, tzinfo=settings.US_EASTERN_TZ)
    monkeypatch.setattr(settings, "CN_MARKET_HOLIDAYS", frozenset({"2026-07-28"}))
    monkeypatch.setattr(settings, "US_MARKET_HOLIDAYS", frozenset({"2026-07-28"}))

    assert not is_cn_a_share_trading_day(moment)
    assert not is_us_equity_trading_day(moment)
