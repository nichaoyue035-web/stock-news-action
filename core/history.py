from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from config import settings
from utils.notifier import log_error

HISTORY_FIELDS = (
    "Date",
    "Name",
    "Code",
    "Start_Price",
    "Reason",
    "Strategy",
    "Observation_Days",
)


def _ensure_history_schema(path: str) -> bool:
    """Upgrade old five-column history files without losing old observations."""
    if not os.path.isfile(path):
        return True
    try:
        with open(path, "r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames == list(HISTORY_FIELDS):
                return True
            rows = list(reader)
        temp_path = f"{path}.{os.getpid()}.tmp"
        with open(temp_path, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=HISTORY_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        field: row.get(field, "")
                        for field in HISTORY_FIELDS
                    }
                )
        os.replace(temp_path, path)
        return True
    except (OSError, csv.Error) as exc:
        log_error(f"❌ 历史记录格式迁移失败: {exc.__class__.__name__}")
        return False


def _append_history(pick_data: dict[str, Any], start_price: str) -> bool:
    try:
        today_str = datetime.now(settings.SHA_TZ).strftime("%Y-%m-%d")
        history_dir = os.path.dirname(settings.HISTORY_FILE)
        if history_dir:
            os.makedirs(history_dir, exist_ok=True)
        if not _ensure_history_schema(settings.HISTORY_FILE):
            return False
        file_exists = os.path.isfile(settings.HISTORY_FILE)
        with open(settings.HISTORY_FILE, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(HISTORY_FIELDS)
            writer.writerow(
                [
                    today_str,
                    pick_data["name"],
                    pick_data["code"],
                    start_price,
                    str(pick_data["reason"]).replace("\n", " "),
                    str(pick_data.get("strategy") or "legacy"),
                    str(pick_data.get("observation_days") or ""),
                ]
            )
        return True
    except Exception as exc:
        log_error(f"❌ 历史写入失败: {exc}")
        return False
