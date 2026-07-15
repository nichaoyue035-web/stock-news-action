from __future__ import annotations

import time
from typing import Any

from openai import OpenAI

from config import settings
from utils.notifier import log_error

AI_TIMEOUT_SECONDS = 30
AI_MAX_RETRIES = 2


def _redact_sensitive_text(text: Any) -> str:
    safe_text = str(text or "").replace("\n", " ").strip()
    for secret in (settings.DEEPSEEK_API_KEY,):
        if secret:
            safe_text = safe_text.replace(str(secret), "<redacted>")
    return safe_text[:160] or "未知原因"


def get_ai_response(
    prompt_text, system_role=None, temperature=1.0, model="deepseek-chat"
):
    """
    统一的 AI 调用接口
    :param prompt_text: 用户输入的提示词
    :param system_role: 系统角色设定 (可选)
    :param temperature: 随机度 (0-2)，默认1.0
    :param model: DeepSeek 模型名称，默认 deepseek-chat；需要思考模式时传 deepseek-reasoner
    :return: AI 的回复文本 (str) 或 None
    """
    # 1. 安全检查
    if not settings.DEEPSEEK_API_KEY:
        log_error("🚫 未检测到 DEEPSEEK_API_KEY，跳过 AI 调用")
        return None

    # 2. 构建消息体
    messages = []
    if system_role:
        messages.append({"role": "system", "content": system_role})

    messages.append({"role": "user", "content": prompt_text})

    # 3. 初始化客户端 (这里可以复用，但在 Serverless 环境下每次新建也无妨)
    client = OpenAI(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        timeout=AI_TIMEOUT_SECONDS,
        max_retries=0,
    )

    # 4. 发起请求并处理异常
    last_error = ""
    for attempt in range(1, AI_MAX_RETRIES + 2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            content = resp.choices[0].message.content
            if str(content or "").strip():
                return content
            last_error = "返回空内容"
            log_error(f"❌ DeepSeek API 调用失败: {last_error}")
            return None
        except Exception as exc:
            last_error = _redact_sensitive_text(exc)
            if attempt <= AI_MAX_RETRIES:
                log_error(
                    f"⚠️ DeepSeek API 调用失败，准备重试 ({attempt}/{AI_MAX_RETRIES}): {last_error}"
                )
                time.sleep(min(attempt * 2, 5))
                continue
            log_error(f"❌ DeepSeek API 调用失败: {last_error}")
            return None

    return None
