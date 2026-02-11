from openai import OpenAI
from config import settings
from utils.notifier import log_error, log_info

def get_ai_response(prompt_text, system_role=None, temperature=1.0):
    """
    统一的 AI 调用接口
    :param prompt_text: 用户输入的提示词
    :param system_role: 系统角色设定 (可选)
    :param temperature: 随机度 (0-2)，默认1.0
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
        base_url="https://api.deepseek.com"
    )

    # 4. 发起请求并处理异常
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=temperature
        )
        return resp.choices[0].message.content
    except Exception as e:
        log_error(f"❌ DeepSeek API 调用失败: {e}")
        return None
