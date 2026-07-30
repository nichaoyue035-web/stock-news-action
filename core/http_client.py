"""Shared HTTP behaviour for external data providers."""

from __future__ import annotations

import random
import time
from typing import Any

import requests

from config import settings
from utils.notifier import log_info


def get_random_header() -> dict[str, str]:
    """Generate a browser-like header for public Eastmoney endpoints."""
    return {
        "User-Agent": random.choice(settings.USER_AGENTS),
        "Referer": "https://eastmoney.com/",
    }


def request_get(*args: Any, **kwargs: Any) -> requests.Response:
    """Retry transient GET failures while preserving caller error handling."""
    max_attempts = max(1, settings.HTTP_GET_MAX_ATTEMPTS)
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(*args, **kwargs)
        except requests.RequestException as exc:
            if attempt >= max_attempts:
                raise
            log_info(
                "GET 请求临时失败，准备重试 "
                f"({attempt}/{max_attempts - 1}): {exc.__class__.__name__}"
            )
            time.sleep(settings.HTTP_GET_RETRY_BASE_SECONDS * attempt)
            continue

        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        if status_code == 429 or status_code >= 500:
            if attempt < max_attempts:
                log_info(
                    "GET 请求暂时不可用，准备重试 "
                    f"({attempt}/{max_attempts - 1}): HTTP {status_code}"
                )
                time.sleep(settings.HTTP_GET_RETRY_BASE_SECONDS * attempt)
                continue
            response.raise_for_status()
        if status_code >= 400:
            response.raise_for_status()
        return response

    raise RuntimeError("GET 请求未能返回响应")
