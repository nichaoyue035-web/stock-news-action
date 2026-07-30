"""Best-effort cross-channel notification for systemd service failures."""

from __future__ import annotations

from config import settings
from utils.notifier import log_error, log_info, send_tg


def _notification_targets(failed_unit: str) -> list[tuple[str, str, str]]:
    """Prefer the other bot when a bot-specific scheduled service failed."""
    clean_unit = " ".join(str(failed_unit or "未知服务").split())[:180]
    primary = ("主频道", settings.TG_BOT_TOKEN, settings.TG_CHAT_ID)
    monitor = ("监控频道", settings.TG_BOT_TOKEN_MONITOR, settings.TG_CHAT_ID_MONITOR)
    ordered = [primary, monitor] if "monitor" in clean_unit else [monitor, primary]
    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, token, chat_id in ordered:
        if not token or not chat_id or (token, chat_id) in seen:
            continue
        seen.add((token, chat_id))
        targets.append((label, token, chat_id))
    return targets


def send_failure_alert(failed_unit: str) -> None:
    """Attempt failure delivery through an alternate configured Telegram channel."""
    clean_unit = " ".join(str(failed_unit or "未知服务").split())[:180]
    message = (
        "🔴 服务执行失败\n"
        f"单元：{clean_unit}\n"
        "systemd 已记录非零退出；请查看 journalctl 和该模式独立健康状态。"
    )
    for label, token, chat_id in _notification_targets(clean_unit):
        if send_tg(message, token=token, chat_id=chat_id):
            log_info(f"服务失败通知已发送至{label}")
            return
    log_error("❌ 服务失败通知未能送达任何已配置 Telegram 频道")
    raise RuntimeError("服务失败通知发送失败")
