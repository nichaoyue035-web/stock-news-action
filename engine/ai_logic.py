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

import datetime
from config import SHA_TZ

def run_general_analysis(mode):
    """处理通用分析模式: daily, funds, monitor, periodic, after_market"""
    prompts = load_prompts()
    now = datetime.datetime.now(SHA_TZ)
    
    # 1. 资金流向模式 (funds)
    if mode == "funds":
        top_in, top_out = get_market_funds()
        if not top_in: return
        in_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out])
        prompt = prompts["funds"].format(in_str=in_str, out_str=out_str)
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>💰 主力资金雷达</b>\n\n{resp.choices[0].message.content}")
        except: pass

    # 2. 每日早报 (daily)
    elif mode == "daily":
        news = get_news(None) # 获取24小时新闻
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:40]])
        prompt = prompts["daily"].format(news_txt=news_txt)
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>🌅 股市全景内参</b>\n\n{resp.choices[0].message.content}")
        except: pass

    # 3. 实时监控 (monitor)
    elif mode == "monitor":
        news = get_news(60) # 获取1小时内新闻
        recent_threshold = now - datetime.timedelta(minutes=25)
        fresh_news = [n for n in news if n['datetime'] > recent_threshold]
        if not fresh_news: return

        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(fresh_news[:15])]
        prompt = prompts["monitor"].format(news_list="\n".join(news_titles))
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            content = resp.choices[0].message.content
            alerts_buffer = []
            for line in content.split('\n'):
                if "ALERT|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        idx_match = re.sub(r'\D', '', parts[1])
                        if idx_match:
                            idx = int(idx_match)
                            if idx < len(fresh_news):
                                t = fresh_news[idx]
                                alerts_buffer.append(f"💡 <b>逻辑</b>：{parts[2]}\n📰 <a href='{t['link']}'>{t['title']}</a> ({t['time_str']})")
            if alerts_buffer:
                send_tg("<b>🎯 机会雷达汇总</b>\n\n" + "\n\n〰️〰️〰️〰️〰️\n\n".join(alerts_buffer))
        except: pass

    # 4. 盘中茶歇/收盘总结 (periodic / after_market)
    elif mode in ["periodic", "after_market"]:
        news = get_news(240) 
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:30]])
        prompt = prompts[mode].format(news_txt=news_txt)
        title = "🌇 每日复盘" if mode == "after_market" else "🍵 盘中茶歇"
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>{title}</b>\n\n{resp.choices[0].message.content}")
        except: pass
