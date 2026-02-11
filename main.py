import requests
import time
import os
import datetime
import sys
import re
import json
import random
from datetime import timezone, timedelta
from openai import OpenAI

# === 1. 配置区域 ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
PICK_FILE = "stock_pick.json"  # 💾 记忆文件：存储AI选的股票

# 浏览器身份池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

# 默认 Prompt
DEFAULT_PROMPTS = {
    "daily": "你是投资总监。基于新闻生成《今日盘前内参》：\n{news_txt}\n\n1.核心主线\n2.利好/利空\n3.情绪判断",
    "monitor": "你是短线交易员。请浏览以下快讯，筛选出具有【即时交易价值】或【重要市场影响】的消息。\n列表：\n{news_list}\n\n要求：\n1. 宁缺毋滥，只选重要的。\n2. 对每一条筛选出的消息，给出一句简短深刻的逻辑分析（利好谁？利空谁？预期多大？）。\n3. 严格按格式输出（每条一行）：ALERT|序号|逻辑分析",
    "after_market": "你是复盘专家。基于下午新闻写《收盘复盘》：\n{news_txt}\n\n1.今日赚钱效应\n2.尾盘变化\n3.明日推演",
    "periodic": "快速总结盘中简报：\n{news_txt}",
    "funds": "你是一位资深A股分析师。这是今日行业资金数据：\n\n主力抢筹：\n{in_str}\n\n主力抛售：\n{out_str}\n\n请分析核心风口、避险板块并给出明日态度。",
    "track": "你今天早上推荐了【{name} ({code})】。\n当前行情：现价 {price}，涨跌幅 {pct}%。\n\n作为游资交易员，请评价当前走势：\n1. 是否符合预期？\n2. 操作建议（持仓/补仓/止损/止盈）？\n3. 简短犀利，100字以内。"
}

# === 2. 功能函数 ===

def load_prompts():
    try:
        if os.path.exists("prompts.json"):
            with open("prompts.json", "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return DEFAULT_PROMPTS

def get_random_header():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://eastmoney.com/"
    }

# --- 新闻抓取 ---
def get_news(minutes_lookback=None):
    timestamp = int(time.time() * 1000)
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html?_={timestamp}"
    try:
        resp = requests.get(url, headers=get_random_header(), timeout=15)
        content = resp.text.strip()
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            data = json.loads(content[start_idx : end_idx + 1])
        else: return []
        
        items = data.get('LivesList', [])
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        time_threshold = now - timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
        
        for item in items:
            show_time_str = item.get('showtime')
            try:
                news_time = datetime.datetime.strptime(show_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHA_TZ)
            except: continue
            if news_time < time_threshold: continue
            
            digest = item.get('digest', '')
            title = item.get('title', '')
            if len(title) < 5: title = digest[:50] + "..." if len(digest) > 50 else digest
            title = re.sub(r'<[^>]+>', '', title)
            link = item.get('url_unique') if item.get('url_unique') else "https://kuaixun.eastmoney.com/"
            
            valid_news.append({
                "title": title, 
                "digest": re.sub(r'<[^>]+>', '', digest), 
                "link": link, 
                "time_str": news_time.strftime('%H:%M'),
                "datetime": news_time 
            })
        return valid_news
    except Exception as e:
        print(f"❌ 新闻抓取失败: {e}")
        return []

# --- 资金流向 ---
def get_market_funds():
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1", 
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90 t:2",
        "fields": "f12,f14,f2,f3,f62" 
    }
    try:
        resp = requests.get(url, headers=get_random_header(), params=params, timeout=10)
        data = resp.json().get('data', {}).get('diff', [])
        sectors = []
        for item in data:
            flow = item.get('f62', 0)
            if flow is None: flow = 0
            sectors.append({
                "name": item.get('f14', '未知'),
                "change": f"{item.get('f3', 0)}%",
                "flow": round(flow / 100000000, 2)
            })
        sectors.sort(key=lambda x: x['flow'], reverse=True)
        return sectors[:8], sectors[-8:]
    except: return [], []

# --- 🆕 真实数据获取 (防幻觉) ---
def get_hot_stocks_data():
    """获取成交额前20的活跃股"""
    print("🔍 正在抓取市场活跃股...")
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "20", "po": "1", "np": "1", 
        "fltt": "2", "invt": "2", "fid": "f6", # 按成交额排序
        "fs": "m:0+t:6,m:0+t:80", # 沪深A股
        "fields": "f12,f14,f3,f6" # 代码, 名称, 涨幅, 成交额
    }
    try:
        resp = requests.get(url, headers=get_random_header(), params=params, timeout=10)
        data = resp.json().get('data', {}).get('diff', [])
        stock_list = []
        for item in data:
            stock_list.append({
                "name": item['f14'],
                "code": item['f12'],
                "pct": f"{item['f3']}%",
                "amount": f"{round(item['f6']/100000000, 1)}亿"
            })
        return stock_list
    except Exception as e:
        print(f"❌ 获取热门股失败: {e}")
        return []

def get_stock_quote(code):
    """获取个股实时行情 (用于验证和追踪)"""
    # 简易判断市场: 6开头为沪市(1), 否则深市(0)
    sec_id = f"1.{code}" if str(code).startswith("6") else f"0.{code}"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={sec_id}&fields=f43,f170,f14" # 现价, 涨跌幅, 名称
    try:
        resp = requests.get(url, headers=get_random_header(), timeout=5)
        data = resp.json().get('data', {})
        if not data: return None
        return {
            "name": data.get('f14', '未知'),
            "price": data.get('f43', '-'),
            "pct": data.get('f170', '-')
        }
    except: return None

# === 3. 核心逻辑 ===

def analyze_and_notify(mode="daily"):
    if not DEEPSEEK_API_KEY:
        print("❌ 错误: 未设置 DEEPSEEK_API_KEY")
        return

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    now = datetime.datetime.now(SHA_TZ)
    is_weekend = now.weekday() >= 5
    PROMPTS = load_prompts()
    
    print(f"🤖 启动模式: [{mode}] | 时间: {now.strftime('%H:%M')}")

    # ----------------------------------------
    # 🌟 模式1: 早盘推荐 (防幻觉版)
    # ----------------------------------------
    if mode == "recommend":
        if is_weekend: return
        
        # 1. 获取真实候选池
        candidates = get_hot_stocks_data()
        if not candidates:
            print("⚠️ 无候选数据，跳过")
            return
            
        candidates_str = "\n".join([f"- {s['name']} (代码:{s['code']}, 涨幅:{s['pct']}, 成交:{s['amount']})" for s in candidates])
        
        # 2. 获取新闻背景
        news = get_news(720)
        news_txt = "\n".join([f"- {n['title']}" for n in news[:15]])
        
        # 3. 极度严格的 Prompt
        prompt = (
            "你是极其理性的量化交易员。请从下方的【候选股票列表】中，挑选唯一一只最符合当前市场热点和新闻面的股票。\n\n"
            f"【候选股票列表】(必须从中选，不可编造):\n{candidates_str}\n\n"
            f"【近期新闻】:\n{news_txt}\n\n"
            "要求：\n"
            "1. 必须从候选列表中选一只，绝对禁止捏造不存在的股票。\n"
            "2. 结合新闻判断哪个板块有机会。\n"
            "3. 输出JSON格式：{\"name\": \"股票名\", \"code\": \"6位代码\", \"reason\": \"简短理由(50字内)\"}"
        )
        
        try:
            # temperature=0.1 降低创造性，强迫其遵守事实
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], temperature=0.1)
            content = resp.choices[0].message.content
            
            # 提取 JSON
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                pick_data = json.loads(json_match.group())
            else:
                print("❌ AI 未输出 JSON")
                return

            # 🛡️ 二次验真 (Double Check)
            real_quote = get_stock_quote(pick_data['code'])
            if not real_quote:
                print(f"❌ 防御拦截: 代码 {pick_data['code']} 无法查询行情")
                return

            # ✅ 验证通过，保存记忆
            with open(PICK_FILE, "w", encoding="utf-8") as f:
                json.dump(pick_data, f, ensure_ascii=False, indent=2)
            
            send_tg(f"<b>🎯 今日AI精选 (防幻觉版)</b>\n\n🦄 <b>{pick_data['name']} ({pick_data['code']})</b>\n当前价: {real_quote['price']}\n\n📝 <b>逻辑：</b>\n{pick_data['reason']}")
            
        except Exception as e:
            print(f"❌ Recommend Error: {e}")

    # ----------------------------------------
    # 🌟 模式2: 盘中/盘后追踪 (Track)
    # ----------------------------------------
    elif mode == "track":
        if is_weekend: return
        
        if not os.path.exists(PICK_FILE):
            print("⚠️ 没有找到今日选股记录 (stock_pick.json)，跳过追踪")
            return
            
        try:
            with open(PICK_FILE, "r", encoding="utf-8") as f:
                pick_data = json.load(f)
            
            code = pick_data.get("code")
            name = pick_data.get("name")
            
            quote = get_stock_quote(code)
            if not quote: return

            prompt = PROMPTS.get("track", DEFAULT_PROMPTS["track"]).format(
                name=name, code=code, price=quote['price'], pct=quote['pct']
            )
            
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            
            icon = "🔴" if float(quote['pct']) > 0 else "🟢"
            send_tg(f"<b>👀 选股跟踪: {name}</b>\n\n{icon} 现价: {quote['price']} ({quote['pct']}%)\n\n🧠 <b>AI观点：</b>\n{resp.choices[0].message.content}")
            
        except Exception as e:
            print(f"❌ Track Error: {e}")

    # ----------------------------------------
    # 原有模式 (Daily, Monitor, Funds, etc.)
    # ----------------------------------------
    elif mode == "funds":
        if is_weekend: return
        top_in, top_out = get_market_funds()
        if not top_in: return
        in_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out])
        prompt = PROMPTS["funds"].format(in_str=in_str, out_str=out_str)
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>💰 主力资金雷达</b>\n\n{resp.choices[0].message.content}")
        except: pass

    elif mode == "daily":
        if is_weekend: return
        news = get_news(None)
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:40]])
        prompt = PROMPTS["daily"].format(news_txt=news_txt)
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>🌅 股市全景内参</b>\n\n{resp.choices[0].message.content}")
        except: pass

    elif mode == "monitor":
        if is_weekend: return
        news = get_news(60)
        recent_threshold = now - timedelta(minutes=25)
        fresh_news = [n for n in news if n['datetime'] > recent_threshold]
        if not fresh_news: return

        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(fresh_news[:15])]
        prompt = PROMPTS["monitor"].format(news_list="\n".join(news_titles))
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            content = resp.choices[0].message.content
            alerts_buffer = []
            for line in content.split('\n'):
                if "ALERT|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        idx = int(re.sub(r'\D', '', parts[1]))
                        if idx < len(fresh_news):
                            t = fresh_news[idx]
                            alerts_buffer.append(f"💡 <b>逻辑</b>：{parts[2]}\n📰 <a href='{t['link']}'>{t['title']}</a> ({t['time_str']})")
            if alerts_buffer:
                send_tg("<b>🎯 机会雷达汇总</b>\n\n" + "\n\n〰️〰️〰️〰️〰️\n\n".join(alerts_buffer))
        except: pass

    elif mode == "periodic":
        news = get_news(240) 
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:20]])
        prompt = PROMPTS["periodic"].format(news_txt=news_txt)
        title = "🌴 周末要闻" if is_weekend else "🍵 盘中茶歇"
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>{title}</b>\n\n{resp.choices[0].message.content}")
        except: pass

    elif mode == "after_market":
        news = get_news(240)
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:35]])
        prompt = PROMPTS["after_market"].format(news_txt=news_txt)
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>🌇 每日复盘</b>\n\n{resp.choices[0].message.content}")
        except: pass

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: requests.post(url, json=data, timeout=10)
    except: pass

if __name__ == "__main__":
    mode = "daily"
    if len(sys.argv) > 1: mode = sys.argv[1]
    analyze_and_notify(mode)
