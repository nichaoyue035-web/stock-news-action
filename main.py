import requests
import time
import os
import datetime
import sys
import re
import json
from datetime import timezone, timedelta
from openai import OpenAI

# === 1. 配置区域 ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

# === 2. 功能函数 ===

def get_news(minutes_lookback=None):
    """获取东方财富 7x24 快讯"""
    timestamp = int(time.time() * 1000)
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html?_={timestamp}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://kuaixun.eastmoney.com/",
        "Accept": "*/*"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        content = resp.text.strip()
        if content.startswith("var "): content = content.split("=", 1)[1].strip()
        if content.endswith(";"): content = content[:-1]
        
        data = json.loads(content)
        items = data.get('LivesList', [])
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        
        if minutes_lookback:
            time_threshold = now - timedelta(minutes=minutes_lookback + 5)
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
            
            valid_news.append({"title": title, "digest": re.sub(r'<[^>]+>', '', digest), "link": link, "time": news_time.strftime('%H:%M')})
        return valid_news
    except Exception as e:
        print(f"❌ 新闻抓取失败: {e}")
        return []

def get_market_funds():
    """获取东方财富-行业板块资金流向 (主力净流入)"""
    # 接口参数：按主力净流入(f62)排序
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1", 
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90 t:2",
        "fields": "f12,f14,f2,f3,f62" # f14:名称, f3:涨幅, f62:主力净流入
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json().get('data', {}).get('diff', [])
        
        # 整理数据
        sectors = []
        for item in data:
            flow = item.get('f62', 0)
            if flow is None: flow = 0
            # 转换为 亿元
            flow_亿 = round(flow / 100000000, 2)
            sectors.append({
                "name": item.get('f14', '未知'),
                "change": f"{item.get('f3', 0)}%",
                "flow": flow_亿
            })
            
        # 排序：净流入最多的前8，和净流出最多的前8
        sectors.sort(key=lambda x: x['flow'], reverse=True)
        top_in = sectors[:8]
        top_out = sectors[-8:]
        top_out.sort(key=lambda x: x['flow']) # 让流出最多的排前面
        
        return top_in, top_out
    except Exception as e:
        print(f"❌ 资金流抓取失败: {e}")
        return [], []

def analyze_and_notify(mode="daily"):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    
    # === 模式: 资金流向分析 ===
    if mode == "funds":
        print("💰 正在分析主力资金流向...")
        top_in, top_out = get_market_funds()
        if not top_in: return
        
        # 构建数据文本
        in_str = "\n".join([f"- {s['name']}: 净流入 {s['flow']}亿 (涨跌 {s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: 净流出 {s['flow']}亿 (涨跌 {s['change']})" for s in top_out])
        
        prompt = f"""
        你是一位资深A股资金分析师。这是今日收盘后的【行业资金流向】数据：

        🚀 【主力抢筹榜】(净流入前8)：
        {in_str}

        📉 【主力抛售榜】(净流出前8)：
        {out_str}

        请简短分析：
        1. **核心风口**：资金在疯狂买入什么逻辑？(是科技、消费还是权重？)
        2. **避险板块**：资金在抛弃什么？
        3. **结论**：结合资金态度，判断明日是“做多情绪强”还是“防守为主”。
        """
        
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            summary = resp.choices[0].message.content
            
            # 发送消息
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            msg = f"<b>💰 主力资金雷达 ({current_date})</b>\n\n{summary}\n\n<i>(数据来源: 东方财富)</i>"
            send_tg(msg)
        except Exception as e:
            print(f"AI 分析失败: {e}")

    # === 其他模式 (新闻类) ===
    else:
        # 获取新闻
        if mode == "daily": news = get_news(None)
        elif mode == "monitor": news = get_news(25)
        elif mode == "periodic": news = get_news(240)
        elif mode == "after_market": news = get_news(240)
        else: return
        
        if not news:
            print("📭 无新闻")
            return

        # --- 这里是之前的新闻处理逻辑 ---
        if mode == "daily":
            # 早报逻辑
            news_txt = "\n".join([f"- {n['title']}" for n in news[:40]])
            prompt = f"你是投资总监。基于新闻生成《今日盘前内参》：\n{news_txt}\n\n1.核心主线\n2.利好/利空\n3.情绪判断"
            try:
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                send_tg(f"<b>🌅 股市全景内参</b>\n\n{resp.choices[0].message.content}")
            except: pass

        elif mode == "monitor":
            # 监控逻辑 (带过滤)
            news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(news[:15])]
            prompt = f"你是短线交易员。筛选有交易价值的快讯(业绩/重组/政策)：\n{chr(10).join(news_titles)}\n输出格式：ALERT|序号|点评"
            try:
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                if "ALERT|" in resp.choices[0].message.content:
                    for line in resp.choices[0].message.content.split('\n'):
                        if "ALERT|" in line:
                            parts = line.split("|")
                            if len(parts) >= 3:
                                idx = int(re.sub(r'\D', '', parts[1]))
                                if idx < len(news):
                                    t = news[idx]
                                    send_tg(f"<b>🚨 机会雷达</b>\n\n💡 {parts[2]}\n\n📰 <a href='{t['link']}'>{t['title']}</a>\n⏰ {t['time']}")
            except: pass

        elif mode == "after_market":
            # 复盘逻辑
            news_txt = "\n".join([f"- {n['title']}" for n in news[:35]])
            prompt = f"你是复盘专家。基于下午新闻写《收盘复盘》：\n{news_txt}\n\n1.今日赚钱效应\n2.尾盘变化\n3.明日推演"
            try:
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                send_tg(f"<b>🌇 每日复盘</b>\n\n{resp.choices[0].message.content}")
            except: pass
            
        elif mode == "periodic":
             # 盘中快报逻辑
            news_txt = "\n".join([f"- {n['title']}" for n in news[:20]])
            prompt = f"快速总结盘中简报：\n{news_txt}"
            try:
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                send_tg(f"<b>🍵 盘中茶歇</b>\n\n{resp.choices[0].message.content}")
            except: pass

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: requests.post(url, json=data, headers=headers, timeout=10)
    except: pass

if __name__ == "__main__":
    # 如果是 monitor 模式（通常是 Push 触发），发送启动通知
    if len(sys.argv) > 1 and sys.argv[1] == "monitor":
        send_tg("🚀 收到 Push！代码更新，正在运行监控...")

    mode = "daily"
    if len(sys.argv) > 1: mode = sys.argv
