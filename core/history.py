from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from config import settings
from utils.notifier import log_error


def _append_history(pick_data: dict[str, Any], start_price: str) -> None:
    try:
        today_str = datetime.now(settings.SHA_TZ).strftime("%Y-%m-%d")
        file_exists = os.path.isfile(settings.HISTORY_FILE)
        with open(settings.HISTORY_FILE, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(["Date", "Name", "Code", "Start_Price", "Reason"])
            writer.writerow([
                today_str,
                pick_data["name"],
                pick_data["code"],
                start_price,
                str(pick_data["reason"]).replace("\n", " "),
            ])
    except Exception as exc:
        log_error(f"❌ 历史写入失败: {exc}")
