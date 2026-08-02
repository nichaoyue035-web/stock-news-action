from __future__ import annotations

import html
import logging
import os
import re
from typing import Any, Iterable, Optional

import requests

from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("StockBot")

SAFE_TELEGRAM_CHUNK_LENGTH = 3900
STATUS_CALLBACK_DATA = "system:status"


def status_button_markup() -> dict[str, Any]:
    """Return the compact status control shared by every Telegram message."""
    return {
        "inline_keyboard": [
            [{"text": "📊 状态", "callback_data": STATUS_CALLBACK_DATA}]
        ]
    }


def with_status_button(reply_markup: dict[str, Any]) -> dict[str, Any]:
    """Append the shared status control without changing existing message actions."""
    markup = dict(reply_markup)
    keyboard = markup.get("inline_keyboard")
    if not isinstance(keyboard, list):
        return status_button_markup()
    rows = [list(row) for row in keyboard if isinstance(row, list)]
    has_status_button = any(
        isinstance(button, dict)
        and button.get("callback_data") == STATUS_CALLBACK_DATA
        for row in rows
        for button in row
    )
    if not has_status_button:
        rows.append(status_button_markup()["inline_keyboard"][0])
    markup["inline_keyboard"] = rows
    return markup


def log_info(message):
    logger.info(message)


def log_error(message):
    logger.error(message)


def _is_ci() -> bool:
    return os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"


def _raise_in_ci(message: str) -> None:
    if _is_ci():
        raise RuntimeError(message)


def _prepare_content(content) -> str:
    """Return Telegram-safe plain text without HTML/Markdown markup."""
    text = html.unescape(str(content))
    extracted_links: list[str] = []

    def _replace_anchor(match: re.Match[str]) -> str:
        url = match.group(2).strip()
        label = re.sub(r"<[^>]*>", "", match.group(3)).strip()
        if url:
            extracted_links.append(url)
        return label or url

    text = re.sub(
        r"<a\s+[^>]*href\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>",
        _replace_anchor,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[<>]", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)

    unique_links = []
    for link in extracted_links:
        if link and link not in text and link not in unique_links:
            unique_links.append(link)
    if unique_links:
        text = text.rstrip() + "\n\n链接：\n" + "\n".join(unique_links)

    return text


def _split_message(
    content: str, max_length: int = SAFE_TELEGRAM_CHUNK_LENGTH
) -> Iterable[str]:
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


def send_tg(content, token=None, chat_id=None) -> bool:
    use_token = token if token else settings.TG_BOT_TOKEN
    use_chat_id = chat_id if chat_id else settings.TG_CHAT_ID

    if not use_token or not use_chat_id:
        message = "⚠️ TG_BOT_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 推送"
        logger.warning(message)
        _raise_in_ci(message)
        return False

    url = f"https://api.telegram.org/bot{use_token}/sendMessage"
    safe_content = _prepare_content(content)
    chunks = list(_split_message(safe_content))

    for index, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": use_chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
            "reply_markup": status_button_markup(),
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
        except requests.RequestException as exc:
            message = f"❌ Telegram 请求异常: {exc.__class__.__name__}"
            logger.error(message)
            _raise_in_ci(message)
            return False

        if resp.status_code != 200:
            response_preview = resp.text[:300].replace(use_token, "<redacted>")
            message = f"❌ Telegram 推送失败: {resp.status_code} - {response_preview}"
            logger.error(message)
            _raise_in_ci(f"Telegram 推送失败: {resp.status_code}")
            return False

        if len(chunks) > 1:
            logger.info("✅ Telegram 分段推送成功 (%s/%s)", index, len(chunks))
        else:
            logger.info("✅ Telegram 推送成功")
    return True


def send_tg_interactive(
    content: Any,
    *,
    reply_markup: dict[str, Any],
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Optional[int]:
    """Send one short Telegram message with callback buttons.

    Interactive radar messages intentionally stay below Telegram's message limit,
    because callback buttons belong to one concrete candidate message.  Return the
    Telegram message ID only after delivery succeeds so it can be audited later.
    """
    use_token = token if token else settings.TG_BOT_TOKEN
    use_chat_id = chat_id if chat_id else settings.TG_CHAT_ID
    if not use_token or not use_chat_id:
        message = "⚠️ Telegram 交互机器人未配置，无法发送雷达候选"
        logger.warning(message)
        _raise_in_ci(message)
        return None

    safe_content = _prepare_content(content)
    if len(safe_content) > SAFE_TELEGRAM_CHUNK_LENGTH:
        message = "❌ 交互雷达消息过长，拒绝发送以避免按钮与内容分离"
        logger.error(message)
        _raise_in_ci(message)
        return None

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{use_token}/sendMessage",
            json={
                "chat_id": use_chat_id,
                "text": safe_content,
                "disable_web_page_preview": True,
                "reply_markup": with_status_button(reply_markup),
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        message = f"❌ Telegram 交互消息请求异常: {exc.__class__.__name__}"
        logger.error(message)
        _raise_in_ci(message)
        return None

    if response.status_code != 200:
        response_preview = response.text[:300].replace(use_token, "<redacted>")
        message = f"❌ Telegram 交互消息发送失败: {response.status_code} - {response_preview}"
        logger.error(message)
        _raise_in_ci(f"Telegram 交互消息发送失败: {response.status_code}")
        return None

    try:
        message_id = response.json().get("result", {}).get("message_id")
        return int(message_id)
    except (TypeError, ValueError, AttributeError):
        logger.error("❌ Telegram 交互消息缺少 message_id")
        return None
