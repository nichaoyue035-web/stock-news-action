import requests
import time
import random
import os
import datetime
from datetime import timezone, timedelta
from openai import OpenAI # 引入 AI 库

# === 配置区域 ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# 北京时区
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

def random_wait():
    """随机等待"""
    if os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch':
        print("⚡ 手动触发，跳过等待")
        return
    wait_seconds = random.randint(0, 7200)
    print(f"💤 计划睡眠 {wait_seconds/60:.1f} 分钟...")
    time.sleep(wait_seconds)

def get_news():
    """抓取新闻，返回 raw_data 用于给 AI 读，以及 html_list 用于展示"""
    print("🔍 正在抓取新浪财经...")
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        items = data['result']['data']
        
        raw_news_text = [] # 给 AI 看的纯文本
        html_news_list = [] # 给 Telegram 看的带链接文本
        
        now = datetime.datetime.now(SHA_TZ)
        one_day_ago = now - timedelta(hours=24)
        
        for item in items:
            pub_time = datetime.datetime.fromtimestamp(int(item['ctime']), SHA_TZ)
            if pub_time < one_day_ago: continue
            
            # 清洗标题
            title = item.get('rich_text', item.get('title', '')).replace('<b>','').replace('</b>','').replace('<font color="red">','').replace('</font>','')
            link = item.get('url', '')
            
            # 存入列表
            raw_news_text.append(f"- {title}")
            html_news_list.append(f"• <a href='{link}'>{title}</a> ({pub_time.strftime('%H:%M')})")
            
        # 限制数量，给 AI 太多它会晕，且容易超时
        return raw_news_text[:15], html_news_list[:15]
        
    except Exception as e:
        print(f"❌ 抓取错误: {e}")
        return [], []

def ask_ai_summary(news_text_list):
    """调用 DeepSeek 进行总结"""
    if not DEEPSEEK_API_KEY:
        return "⚠️ 未配置 AI Key，无法生成总结。"
    
    if not news_text_list:
        return "今日无重要新闻。"

    print("🧠 正在请求 AI 大脑进行分析...")
    
    # 拼接新闻文本
    news_content = "\n".join(news_text_list)
    
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY, 
        base_url="https://api.deepseek.com" # DeepSeek 的地址
    )

    prompt = f"""
    你是专业的华尔街交易员，语言风格简练、犀利。
    请阅读以下过去24小时的中国财经新闻标题：
    
    {news_content}
    
    任务：
    1. 用一句话概括今日市场情绪（例如：极度贪婪/恐慌/观望）。
    2. 提炼 3 个最重要的市场信号（用 emoji 开头）。
    3. 如果有明显利好或利空板块，请直接点名。
    
    输出格式要求：直接输出内容，不要废话，不要用Markdown代码块包裹。
    """

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ AI 调用失败: {e}")
        return "🤖 AI 睡着了，本次总结失败。"

def send_tg(summary, news_links):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 缺少 TG 密钥")
        return
    
    # 组装最终消息
    date_str = datetime.datetime.now(SHA_TZ).strftime("%Y-%m-%d")
    
    # 消息结构：AI 总结 + 分割线 + 新闻列表
    final_content = (
        f"<b>🤖 AI 市场内参 ({date_str})</b>\n\n"
        f"{summary}\n\n"
        f"<b>📰 原始消息源：</b>\n" + 
        "\n".join(news_links)
    )
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {
        "chat_id": TG_CHAT_ID,
        "text": final_content,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    resp = requests.post(url, json=data, headers=headers)
    print(f"📡 推送结果: {resp.status_code}")

if __name__ == "__main__":
    random_wait()
    
    # 1. 抓新闻
    raw_news, html_news = get_news()
    
    if raw_news:
        # 2. 只有抓到新闻了，才叫 AI
        ai_result = ask_ai_summary(raw_news)
        
        # 3. 发送
        send_tg(ai_result, html_news)
        print("✅ 任务完成")
    else:
        print("📭 没抓到新闻，不发送。")
