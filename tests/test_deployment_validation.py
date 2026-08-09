from __future__ import annotations

from pathlib import Path

from scripts.validate_deployment import validate_systemd_units


def test_checked_in_systemd_manifests_are_valid():
    assert validate_systemd_units() == []


def test_manifest_validator_reports_timer_without_target(tmp_path: Path):
    (tmp_path / "broken.timer").write_text(
        "[Timer]\nOnCalendar=*-*-* *:*:00\nPersistent=true\nUnit=missing.service\n",
        encoding="utf-8",
    )

    errors = validate_systemd_units(tmp_path)

    assert "broken.timer: 定时目标 missing.service 没有对应服务文件 missing.service" in errors
