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

# 浏览器身份池 (用于伪装，防止被封 IP)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

# 默认 Prompt 备份
DEFAULT_PROMPTS = {
    "daily": "你是投资总监。基于新闻生成《今日盘前内参》：\n{news_txt}\n\n1.核心主线\n2.利好/利空\n3.情绪判断",
    "monitor": "你是短线交易员。请浏览以下快讯，筛选出具有【即时交易价值】或【重要市场影响】的消息。\n列表：\n{news_list}\n\n要求：\n1. 宁缺毋滥，只选重要的。\n2. 对每一条筛选出的消息，给出一句简短深刻的逻辑分析（利好谁？利空谁？预期多大？）。\n3. 严格按格式输出（每条一行）：ALERT|序号|逻辑分析",
    "after_market": "你是复盘专家。基于下午新闻写《收盘复盘》：\n{news_txt}\n\n1.今日赚钱效应\n2.尾盘变化\n3.明日推演",
    "periodic": "快速总结盘中简报：\n{news_txt}",
    "funds": "你是一位资深A股分析师。这是今日行业资金数据：\n\n主力抢筹：\n{in_str}\n\n主力抛售：\n{out_str}\n\n请分析核心风口、避险板块并给出明日态度。"
}

# === 2. 功能函数 ===

def load_prompts():
    """尝试从 prompts.json 加载提示词"""
    try:
        if os.path.exists("prompts.json"):
            with open("prompts.json", "r", encoding="utf-8") as f:
                print("✅ 成功加载外部提示词配置 (prompts.json)")
                return json.load(f)
    except Exception as e:
        print(f"⚠️ 加载 prompts.json 失败: {e}，将使用内置默认值")
    return DEFAULT_PROMPTS

def get_random_header():
    """随机获取一个请求头，伪装身份"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://kuaixun.eastmoney.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
    }

def get_news(minutes_lookback=None):
    """获取东方财富 7x24 快讯 (增强防御版)"""
    timestamp = int(time.time() * 1000)
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html?_={timestamp}"
    
    try:
        print(f"🔍 正在抓取新闻 (回溯 {minutes_lookback if minutes_lookback else 1440} 分钟)...")
        # ✅ 修正点：这里调用了 get_random_header()
        resp = requests.get(url, headers=get_random_header(), timeout=15)
        
        # === 🛡️ 核心改进：智能解析 JSON ===
        content = resp.text.strip()
        # ✅ 修正点：不再用 split，而是自动寻找 JSON 边界
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        
        if start_idx != -1 and end_idx != -1:
            json_str = content[start_idx : end_idx + 1]
            data = json.loads(json_str)
        else:
            print("⚠️ 警告: 无法从响应中提取 JSON 数据，接口格式可能已变更")
            return []
        # ================================
        
        items = data.get('LivesList', [])
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        
        if minutes_lookback:
            time_threshold = now - timedelta(minutes=minutes_lookback)
        else:
            time_threshold = now - timedelta(hours=24)
        
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
        
        print(f"✅ 抓取成功，符合时间范围的新闻共 {len(valid_news)} 条")
        return valid_news
    except Exception as e:
        print(f"❌ 新闻抓取失败: {e}")
        return []

def get_market_funds():
    """获取资金流向 (增强防御版)"""
    print("🔍 正在抓取资金流向数据...")
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1", 
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90 t:2",
        "fields": "f12,f14,f2,f3,f62" 
    }
    try:
        # ✅ 修正点：这里也加了 header 伪装
        resp = requests.get(url, headers=get_random_header(), params=params, timeout=10)
        data = resp.json().get('data', {}).get('diff', [])
        
        sectors = []
        for item in data:
            flow = item.get('f62', 0)
            if flow is None: flow = 0
            flow_亿 = round(flow / 100000000, 2)
            sectors.append({
                "name": item.get('f14', '未知'),
                "change": f"{item.get('f3', 0)}%",
                "flow": flow_亿
            })
            
        sectors.sort(key=lambda x: x['flow'], reverse=True)
        return sectors[:8], sectors[-8:] # Top In, Top Out
    except Exception as e:
        print(f"❌ 资金流抓取失败: {e}")
        return [], []

def analyze_and_notify(mode="daily"):
    if not DEEPSEEK_API_KEY:
        print("❌ 错误: 未设置 DEEPSEEK_API_KEY")
        return

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    
    # === 周末判断逻辑 ===
    now = datetime.datetime.now(SHA_TZ)
    is_weekend = now.weekday() >= 5  # 5=周六, 6=周日
    print(f"🤖 启动模式: [{mode}] | 当前时间: {now.strftime('%A %H:%M')} | 周末: {is_weekend}")
    
    PROMPTS = load_prompts()
    
    # 1. 资金流模式
    if mode == "funds":
        if is_weekend:
            print("😴 周末休市，资金流模式跳过")
            return
        top_in, top_out = get_market_funds()
        if not top_in: return
        
        in_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out])
        prompt = PROMPTS["funds"].format(in_str=in_str, out_str=out_str)
        
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>💰 主力资金雷达 ({now.strftime('%m-%d')})</b>\n\n{resp.choices[0].message.content}")
        except Exception as e: print(f"❌ Funds Error: {e}")

    # 2. 日报模式
    elif mode == "daily":
        if is_weekend:
            print("😴 周末休市，Daily 日报模式跳过")
            return
            
        news = get_news(None)
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:40]])
        prompt = PROMPTS["daily"].format(news_txt=news_txt)
        
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>🌅 股市全景内参</b>\n\n{resp.choices[0].message.content}")
        except Exception as e: print(f"❌ Daily Error: {e}")

    # 3. 监控模式
    elif mode == "monitor":
        if is_weekend:
            print("😴 周末休市，Monitor 监控模式跳过")
            return

        news = get_news(60)
        if not news: return
        
        recent_threshold = now - timedelta(minutes=25)
        fresh_news = [n for n in news if n['datetime'] > recent_threshold]
        
        if not fresh_news:
            print("📭 无 25 分钟内的新增消息，跳过推送")
            return

        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(fresh_news[:15])]
        prompt = PROMPTS["monitor"].format(news_list="\n".join(news_titles))
        
        try:
            print(f"🧠 AI 正在分析 {len(fresh_news)} 条最新消息...")
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            content = resp.choices[0].message.content
            
            alerts_buffer = []
            for line in content.split('\n'):
                if "ALERT|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        idx_str = re.sub(r'\D', '', parts[1])
                        if idx_str:
                            idx = int(idx_str)
                            if idx < len(fresh_news):
                                t = fresh_news[idx]
                                item_str = f"💡 <b>逻辑</b>：{parts[2]}\n📰 <a href='{t['link']}'>{t['title']}</a> ({t['time_str']})"
                                alerts_buffer.append(item_str)
            
            if alerts_buffer:
                send_tg("<b>🎯 机会雷达汇总</b>\n\n" + "\n\n〰️〰️〰️〰️〰️\n\n".join(alerts_buffer))
        except Exception as e: print(f"❌ Monitor Error: {e}")

    # 4. 周期模式 / 周末模式
    elif mode == "periodic":
        news = get_news(240) 
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:20]])
        prompt = PROMPTS["periodic"].format(news_txt=news_txt)
        
        title = "🌴 周末要闻" if is_weekend else "🍵 盘中茶歇"
        
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>{title}</b>\n\n{resp.choices[0].message.content}")
        except Exception as e: print(f"❌ Periodic Error: {e}")

    elif mode == "after_market":
        news = get_news(240)
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:35]])
        prompt = PROMPTS["after_market"].format(news_txt=news_txt)
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            send_tg(f"<b>🌇 每日复盘</b>\n\n{resp.choices[0].message.content}")
        except Exception as e: print(f"❌ After Market Error: {e}")

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: requests.post(url, json=data, timeout=10)
    except Exception as e: print(f"❌ TG Error: {e}")

if __name__ == "__main__":
    mode = "daily"
    if len(sys.argv) > 1: mode = sys.argv[1]
    analyze_and_notify(mode)
