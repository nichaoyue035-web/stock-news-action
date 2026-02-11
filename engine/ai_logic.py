import json
import os
import re
from openai import OpenAI
from config import DEEPSEEK_API_KEY, PICK_FILE, DEFAULT_PROMPTS
from data_sources.market_api import get_stock_quote, get_hot_stocks_data
from data_sources.news_api import get_news
from utils.notifier import send_tg

# 初始化 AI 客户端
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

def load_prompts():
    """从本地文件加载自定义 Prompt，否则使用默认值"""
    try:
        if os.path.exists("prompts.json"):
            with open("prompts.json", "r", encoding="utf-8") as f:
                return json.load(f)
    except: 
        pass
    return DEFAULT_PROMPTS

def run_recommend():
    """AI 选股模式 (防幻觉版)"""
    # 1. 获取真实候选数据
    candidates = get_hot_stocks_data()
    if not candidates: return
    
    candidates_str = "\n".join([f"- {s['name']} (代码:{s['code']}, 涨幅:{s['pct']}, 成交:{s['amount']})" for s in candidates])
    
    # 2. 获取新闻背景
    news = get_news(720)
    news_txt = "\n".join([f"- {n['title']}" for n in news[:15]])
    
    # 3. 构建 Prompt
    prompt = (
        "你是极其理性的量化交易员。请从下方的【候选股票列表】中，挑选唯一一只最符合当前市场热点和新闻面的股票。\n\n"
        f"【候选股票列表】:\n{candidates_str}\n\n"
        f"【近期新闻】:\n{news_txt}\n\n"
        "要求：1. 必须从中选一只；2. 输出 JSON 格式：{\"name\": \"股票名\", \"code\": \"代码\", \"reason\": \"理由\"}"
    )
    
    try:
        resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], temperature=0.1)
        content = resp.choices[0].message.content
        
        # 提取并验证 JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            pick_data = json.loads(json_match.group())
            # 二次验证真实性
            real_quote = get_stock_quote(pick_data['code'])
            if real_quote:
                with open(PICK_FILE, "w", encoding="utf-8") as f:
                    json.dump(pick_data, f, ensure_ascii=False, indent=2)
                send_tg(f"<b>🎯 今日AI精选</b>\n\n🦄 <b>{pick_data['name']} ({pick_data['code']})</b>\n\n📝 <b>逻辑：</b>\n{pick_data['reason']}")
    except Exception as e:
        print(f"❌ Recommend Error: {e}")

def run_track():
    """行情追踪模式"""
    if not os.path.exists(PICK_FILE): return
    
    try:
        with open(PICK_FILE, "r", encoding="utf-8") as f:
            pick_data = json.load(f)
        
        quote = get_stock_quote(pick_data['code'])
        if not quote: return

        prompts = load_prompts()
        track_prompt = prompts.get("track", DEFAULT_PROMPTS["track"]).format(
            name=pick_data['name'], code=pick_data['code'], price=quote['price'], pct=quote['pct']
        )
        
        resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": track_prompt}])
        icon = "🔴" if float(quote['pct']) > 0 else "🟢"
        send_tg(f"<b>👀 选股跟踪: {pick_data['name']}</b>\n\n{icon} 现价: {quote['price']} ({quote['pct']}%)\n\n🧠 <b>AI观点：</b>\n{resp.choices[0].message.content}")
    except Exception as e:
        print(f"❌ Track Error: {e}")
