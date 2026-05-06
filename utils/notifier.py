from __future__ import annotations

import html
import logging
import os
from typing import Iterable

import requests

from config import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("StockBot")

SAFE_TELEGRAM_CHUNK_LENGTH = 3900


def log_info(message):
    logger.info(message)


def log_error(message):
    logger.error(message)


def _is_ci() -> bool:
    return os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"


def _raise_in_ci(message: str) -> None:
    if _is_ci():
        raise RuntimeError(message)


def _prepare_content(content, parse_mode: str | None) -> str:
    text = str(content)
    if parse_mode and parse_mode.upper() == "HTML":
        return html.escape(text, quote=False)
    return text


def _split_message(content: str, max_length: int = SAFE_TELEGRAM_CHUNK_LENGTH) -> Iterable[str]:
    if len(content) <= max_length:
        yield content
        return

    remaining = content
    while remaining:
        if len(remaining) <= max_length:
            yield remaining
            break

        split_at = remaining.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, max_length)
        if split_at <= 0:
            split_at = max_length

        chunk = remaining[:split_at].rstrip()
        if chunk:
            yield chunk
        remaining = remaining[split_at:].lstrip()


def send_tg(content, token=None, chat_id=None, parse_mode="HTML"):
    use_token = token if token else settings.TG_BOT_TOKEN
    use_chat_id = chat_id if chat_id else settings.TG_CHAT_ID

    if not use_token or not use_chat_id:
        message = "⚠️ TG_BOT_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 推送"
        logger.warning(message)
        _raise_in_ci(message)
        return

    url = f"https://api.telegram.org/bot{use_token}/sendMessage"
    safe_content = _prepare_content(content, parse_mode)
    chunks = list(_split_message(safe_content))

    for index, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": use_chat_id,
            "text": chunk,
            "disable_web_page_preview": True
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(url, json=payload, timeout=10)
        except requests.RequestException as exc:
            message = f"❌ Telegram 请求异常: {exc.__class__.__name__}"
            logger.error(message)
            _raise_in_ci(message)
            return

        if resp.status_code != 200:
            response_preview = resp.text[:300].replace(use_token, "<redacted>")
            message = f"❌ Telegram 推送失败: {resp.status_code} - {response_preview}"
            logger.error(message)
            _raise_in_ci(f"Telegram 推送失败: {resp.status_code}")
            return

        if len(chunks) > 1:
            logger.info("✅ Telegram 分段推送成功 (%s/%s)", index, len(chunks))
        else:
            logger.info("✅ Telegram 推送成功")
