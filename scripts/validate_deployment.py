"""Static validation for the checked-in systemd deployment manifests."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = PROJECT_ROOT / "deploy" / "systemd"


def _read_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    """Parse the small key-value subset used by this project's unit files."""
    sections: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    current_section = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue
        if "=" in line and current_section:
            key, value = line.split("=", 1)
            sections[current_section][key.strip()].append(value.strip())
    return {section: dict(values) for section, values in sections.items()}


def _service_file_for(unit_name: str) -> str:
    """Map an instantiated systemd service name to its checked-in template."""
    if "@" not in unit_name:
        return unit_name
    prefix, suffix = unit_name.split("@", 1)
    if "." not in suffix:
        return unit_name
    extension = suffix.rsplit(".", 1)[1]
    return f"{prefix}@.{extension}"


def _require(
    errors: list[str],
    unit_name: str,
    values: dict[str, dict[str, list[str]]],
    section: str,
    key: str,
    expected: str | None = None,
) -> None:
    actual = values.get(section, {}).get(key, [])
    if not actual:
        errors.append(f"{unit_name}: 缺少 [{section}] {key}")
    elif expected is not None and not any(expected in value for value in actual):
        errors.append(
            f"{unit_name}: [{section}] {key} 应包含 {expected!r}，实际为 {actual!r}"
        )


def validate_systemd_units(systemd_dir: Path = SYSTEMD_DIR) -> list[str]:
    """Return all manifest errors without needing a live systemd host."""
    errors: list[str] = []
    unit_paths = sorted(systemd_dir.glob("*.service"))
    timer_paths = sorted(systemd_dir.glob("*.timer"))
    known_files = {path.name for path in [*unit_paths, *timer_paths]}

    protected_services = {
        "stock-news-action@.service": True,
        "stock-news-action-interaction.service": True,
        "stock-news-action-failure@.service": False,
    }
    for unit_name, requires_failure_hook in protected_services.items():
        path = systemd_dir / unit_name
        if not path.is_file():
            errors.append(f"缺少服务定义：{unit_name}")
            continue
        values = _read_unit(path)
        for key, expected in (
            ("User", "ec2-user"),
            ("Group", "ec2-user"),
            ("WorkingDirectory", "/home/ec2-user/apps/stock-news-action"),
            ("EnvironmentFile", "/home/ec2-user/.config/stock-news-action/environment"),
            ("Environment", "PYTHONUNBUFFERED=1"),
            ("Environment", "STATE_DIR=/var/lib/stock-news-action"),
            ("UMask", "0077"),
            ("ExecStart", "/home/ec2-user/apps/stock-news-action/main.py"),
        ):
            _require(errors, unit_name, values, "Service", key, expected)
        if unit_name != "stock-news-action-failure@.service":
            _require(errors, unit_name, values, "Service", "StateDirectory", "stock-news-action")
        if requires_failure_hook:
            _require(
                errors,
                unit_name,
                values,
                "Unit",
                "OnFailure",
                "stock-news-action-failure@%n.service",
            )

    for path in timer_paths:
        values = _read_unit(path)
        _require(errors, path.name, values, "Timer", "OnCalendar")
        _require(errors, path.name, values, "Timer", "Persistent")
        unit_targets = values.get("Timer", {}).get("Unit", [])
        if not unit_targets:
            errors.append(f"{path.name}: 缺少 [Timer] Unit")
            continue
        for target in unit_targets:
            target_file = _service_file_for(target)
            if target_file not in known_files:
                errors.append(
                    f"{path.name}: 定时目标 {target} 没有对应服务文件 {target_file}"
                )
    return errors


def main() -> int:
    errors = validate_systemd_units()
    if errors:
        print("部署配置校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("部署配置校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
