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
        print(f"🔍 正在抓取新闻 (回溯 {minutes_lookback if minutes_lookback else 1440} 分钟)...")
        resp = requests.get(url, headers=headers, timeout=15)
        content = resp.text.strip()
        if content.startswith("var "): content = content.split("=", 1)[1].strip()
        if content.endswith(";"): content = content[:-1]
        
        data = json.loads(content)
        items = data.get('LivesList', [])
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        
        if minutes_lookback:
            # 修正：这里不再加额外的 5 分钟，保持逻辑清晰，由外部控制
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
            
            valid_news.append({"title": title, "digest": re.sub(r'<[^>]+>', '', digest), "link": link, "time": news_time.strftime('%H:%M')})
        
        print(f"✅ 抓取成功，符合时间范围的新闻共 {len(valid_news)} 条")
        return valid_news
    except Exception as e:
        print(f"❌ 新闻抓取失败: {e}")
        return []

def get_market_funds():
    """获取东方财富-行业板块资金流向 (主力净流入)"""
    print("🔍 正在抓取资金流向数据...")
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1", 
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90 t:2",
        "fields": "f12,f14,f2,f3,f62" 
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
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
        top_in = sectors[:8]
        top_out = sectors[-8:]
        top_out.sort(key=lambda x: x['flow']) 
        
        return top_in, top_out
    except Exception as e:
        print(f"❌ 资金流抓取失败: {e}")
        return [], []

def analyze_and_notify(mode="daily"):
    if not DEEPSEEK_API_KEY:
        print("❌ 错误: 未设置 DEEPSEEK_API_KEY 环境参数")
        return

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    print(f"🤖 AI 客户端已就绪，准备执行模式: [{mode}]")
    
    # === 模式: 资金流向分析 ===
    if mode == "funds":
        print("💰 正在分析主力资金流向...")
        top_in, top_out = get_market_funds()
        if not top_in: 
            print("⚠️ 未获取到资金数据，跳过")
            return
        
        in_str = "\n".join([f"- {s['name']}: 净流入 {s['flow']}亿 (涨跌 {s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: 净流出 {s['flow']}亿 (涨跌 {s['change']})" for s in top_out])
        
        prompt = f"你是一位资深A股分析师。这是今日行业资金数据：\n\n主力抢筹：\n{in_str}\n\n主力抛售：\n{out_str}\n\n请分析核心风口、避险板块并给出明日态度。"
        
        try:
            resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
            summary = resp.choices[0].message.content
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            send_tg(f"<b>💰 主力资金雷达 ({current_date})</b>\n\n{summary}")
        except Exception as e:
            print(f"❌ AI 分析资金流失败: {e}")

    # === 其他模式 (新闻类) ===
    else:
        # ⚠️ 关键修改：放大 Monitor 模式的时间窗口，防止 GitHub 调度延迟导致漏单
        if mode == "daily": news = get_news(None)
        elif mode == "monitor": news = get_news(60) # 改为 60 分钟，覆盖延迟
        elif mode == "periodic": news = get_news(240)
        elif mode == "after_market": news = get_news(240)
        else: 
            print(f"❌ 未知模式: {mode}")
            return
        
        if not news:
            print(f"📭 模式 {mode} 下无符合条件的新闻 (这可能是正常的，但也可能是抓取被拦截)")
            return

        if mode == "daily":
            news_txt = "\n".join([f"- {n['title']}" for n in news[:40]])
            prompt = f"你是投资总监。基于新闻生成《今日盘前内参》：\n{news_txt}\n\n1.核心主线\n2.利好/利空\n3.情绪判断"
            try:
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                send_tg(f"<b>🌅 股市全景内参</b>\n\n{resp.choices[0].message.content}")
            except Exception as e:
                print(f"❌ Daily 模式执行失败: {e}")

elif mode == "monitor":
            # 1. 准备待分析的新闻列表
            news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(news[:15])]
            
            # 2. 优化 Prompt：明确要求筛选并给出逻辑分析
            prompt = f"你是短线交易员。请浏览以下快讯，筛选出具有【即时交易价值】或【重要市场影响】的消息。\n列表：\n{chr(10).join(news_titles)}\n\n要求：\n1. 宁缺毋滥，只选重要的。\n2. 对每一条筛选出的消息，给出一句简短深刻的逻辑分析（利好谁？利空谁？预期多大？）。\n3. 严格按格式输出（每条一行）：ALERT|序号|逻辑分析"
            
            try:
                print("🧠 AI 正在筛选 Monitor 消息...")
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                content = resp.choices[0].message.content
                print(f"🤖 AI 原始回复: {content}") 

                # === 修改开始：不再逐条发送，而是先收集 ===
                alerts_buffer = [] 

                if "ALERT|" not in content:
                    print("⚠️ AI 认为当前无重要机会，未触发推送")
                
                for line in content.split('\n'):
                    if "ALERT|" in line:
                        parts = line.split("|")
                        if len(parts) >= 3:
                            idx_str = re.sub(r'\D', '', parts[1]) # 提取序号
                            if idx_str:
                                idx = int(idx_str)
                                if idx < len(news):
                                    t = news[idx]
                                    # 组装单条内容：加入 Emoji 和 AI 分析
                                    # 格式：💡 分析... \n 📰 标题 (时间)
                                    item_str = f"💡 <b>逻辑</b>：{parts[2]}\n📰 <a href='{t['link']}'>{t['title']}</a> ({t['time']})"
                                    alerts_buffer.append(item_str)
                
                # === 核心修改：如果有内容，合并成一条发送 ===
                if alerts_buffer:
                    # 使用分割线连接多条消息
                    final_msg = "<b>🚨 机会雷达汇总</b>\n\n" + "\n\n〰️〰️〰️〰️〰️\n\n".join(alerts_buffer)
                    send_tg(final_msg)
                # === 修改结束 ===

            except Exception as e:
                print(f"❌ Monitor 模式执行失败: {e}")

        elif mode == "after_market":
            news_txt = "\n".join([f"- {n['title']}" for n in news[:35]])
            prompt = f"你是复盘专家。基于下午新闻写《收盘复盘》：\n{news_txt}\n\n1.今日赚钱效应\n2.尾盘变化\n3.明日推演"
            try:
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                send_tg(f"<b>🌇 每日复盘</b>\n\n{resp.choices[0].message.content}")
            except Exception as e:
                print(f"❌ After Market 模式执行失败: {e}")
            
        elif mode == "periodic":
            news_txt = "\n".join([f"- {n['title']}" for n in news[:20]])
            prompt = f"快速总结盘中简报：\n{news_txt}"
            try:
                resp = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}])
                send_tg(f"<b>🍵 盘中茶歇</b>\n\n{resp.choices[0].message.content}")
            except Exception as e:
                print(f"❌ Periodic 模式执行失败: {e}")

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 缺失 Telegram 配置")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=data, timeout=10)
        if r.status_code != 200:
            print(f"❌ TG 发送失败: {r.text}")
        else:
            print("✅ TG 消息发送成功")
    except Exception as e:
        print(f"❌ TG 请求异常: {e}")

if __name__ == "__main__":
    # 获取运行模式，默认为 daily
    mode = "daily"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    
    print(f"🚀 正在以 [{mode}] 模式启动脚本...")
    
    # 增加心跳显示，确保日志里能看到
    print(f"🕒 系统时间 (UTC): {datetime.datetime.utcnow()}")
    print(f"🕒 系统时间 (北京): {datetime.datetime.now(SHA_TZ)}")

    if mode == "monitor" and os.getenv("GITHUB_EVENT_NAME") == "push":
        send_tg("系统通知：代码已更新，监控任务启动中...")

    analyze_and_notify(mode)
