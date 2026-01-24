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

# 设置北京时区
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

def get_news(minutes_lookback=None):
    """
    【数据源】东方财富 7x24
    """
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
        
        if content.startswith("var "):
            content = content.split("=", 1)[1].strip()
            if content.endswith(";"):
                content = content[:-1]
        
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
                news_time = datetime.datetime.strptime(show_time_str, "%Y-%m-%d %H:%M:%S")
                news_time = news_time.replace(tzinfo=SHA_TZ)
            except:
                continue

            if news_time < time_threshold:
                continue
            
            digest = item.get('digest', '')
            title = item.get('title', '')
            
            if len(title) < 5:
                title = digest[:50] + "..." if len(digest) > 50 else digest
            
            title = re.sub(r'<[^>]+>', '', title)
            digest = re.sub(r'<[^>]+>', '', digest)
            
            link = item.get('url_unique') if item.get('url_unique') else "https://kuaixun.eastmoney.com/"
            
            valid_news.append({
                "title": title,
                "digest": digest,
                "link": link,
                "time": news_time.strftime('%H:%M')
            })
            
        return valid_news
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return []

def analyze_and_notify(news_list, mode="daily"):
    if not news_list:
        print("📭 无新闻")
        return

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    
    # === 模式 A: 每日早报 ===
    if mode == "daily":
        print("📝 正在生成全景早报...")
        news_inputs = []
        for n in news_list[:40]:
            detail = n['digest'][:100] if n['digest'] else "无详情"
            news_inputs.append(f"- [{n['time']}] {n['title']} (内容: {detail})")
        news_text_block = chr(10).join(news_inputs)

        prompt = f"""
        你是一位视野宏大的A股投资总监。请阅读过去24小时的新闻：
        {news_text_block}

        请制作一份**高价值**的《今日盘前内参》。
        【第一部分：核心主线推演】提炼出 1条 最具爆发力的炒作主线。
        【第二部分：其他高价值情报】列出 3-5 条直接利好/利空消息。
        【第三部分：市场情绪风向】一句话总结多空情绪。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat", messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            final_msg = f"<b>🌅 股市全景内参 ({current_date})</b>\n\n{summary}\n\n<i>(AI 辅助决策)</i>"
            send_tg(final_msg)
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")

    # === 模式 B: 周期性快报 ===
    elif mode == "periodic":
        print("🕒 正在生成时段简报...")
        if len(news_list) < 5: return

        news_inputs = []
        for n in news_list[:20]:
            news_inputs.append(f"- [{n['time']}] {n['title']}")
        news_text_block = chr(10).join(news_inputs)

        prompt = f"""
        你是一位即时财经编辑。这是过去几小时的快讯：
        {news_text_block}
        请快速总结一份《盘中时段简报》。列出 2-3 个重点。若无大事则说“消息面平静”。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat", messages=[{"role": "user", "content": prompt}], stream=False
            )
            final_msg = f"<b>🍵 盘中茶歇</b>\n\n{resp.choices[0].message.content}"
            send_tg(final_msg)
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")

    # === 模式 C: 突发监控 ===
    elif mode == "monitor":
        print("⚡️ 监控模式...")
        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(news_list[:15])]
        
        prompt = f"""
        你是一个A股短线交易员。筛选最新快讯：
        {chr(10).join(news_titles)}

        【任务】筛选有**短线交易价值**的消息。
        【标准】保留：业绩、中标、重组、立案、涨价、重磅政策。过滤：行政废话。
        【输出】ALERT|序号|简短提示(利好/利空/题材)。若无机会输出 NO。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat", messages=[{"role": "user", "content": prompt}], stream=False
            )
            ai_reply = resp.choices[0].message.content.strip()
            
            if "ALERT|" in ai_reply:
                for line in ai_reply.split('\n'):
                    if "ALERT|" in line:
                        parts = line.split("|") 
                        if len(parts) >= 3:
                            try:
                                index = int(re.sub(r'\D', '', parts[1]))
                                comment = parts[2]
                                if index < len(news_list):
                                    target = news_list[index]
                                    msg = f"<b>🚨 机会雷达</b>\n\n💡 {comment}\n\n📰 <a href='{target['link']}'>{target['title']}</a>\n⏰ {target['time']}"
                                    send_tg(msg)
                            except: pass
            else:
                print("😴 无交易机会")
        except Exception as e:
            print(f"AI 监控出错: {e}")

    # === 模式 D: 收盘复盘 (新增) ===
    elif mode == "after_market":
        print("🌇 正在生成收盘复盘...")
        # 即使新闻少也尽量总结
        news_inputs = []
        for n in news_list[:35]:
            news_inputs.append(f"- [{n['time']}] {n['title']}")
        news_text_block = chr(10).join(news_inputs)

        prompt = f"""
        你是一位A股超短线复盘专家。这是今日下午及收盘前后的快讯：
        {news_text_block}

        请撰写《今日收盘复盘》。
        1. **核心情绪**：一句话定义今日赚钱效应（如：冰点期/主升浪/退潮期/混沌期）。
        2. **热点回顾**：总结下午盘面的核心变化（是否有资金回流或尾盘跳水）。
        3. **明日剧本**：基于今日收盘，推演明日开盘可能的走势。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat", messages=[{"role": "user", "content": prompt}], stream=False
            )
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            final_msg = f"<b>🌇 每日复盘 ({current_date})</b>\n\n{resp.choices[0].message.content}"
            send_tg(final_msg)
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: requests.post(url, json=data, headers=headers, timeout=10)
    except: pass

if __name__ == "__main__":
    # 仅在 monitor 模式下（通常是 Push 触发）发送启动通知，避免其他定时任务也发
    if len(sys.argv) > 1 and sys.argv[1] == "monitor":
        # 如果你觉得每次监控都发太烦，可以注释掉下面这行
        send_tg("🚀 收到 Push！代码更新，正在运行监控...")
    
    mode = "daily"
    if len(sys.argv) > 1: mode = sys.argv[1]
    
    print(f"🚀 启动 | 模式: {mode}")
    
    if mode == "daily":
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        news = get_news(minutes_lookback=25)
        analyze_and_notify(news, mode="monitor")
    elif mode == "periodic":
        news = get_news(minutes_lookback=240)
        analyze_and_notify(news, mode="periodic")
    elif mode == "after_market":
        # 抓取过去 4 小时 (涵盖整个下午盘)
        news = get_news(minutes_lookback=240)
        analyze_and_notify(news, mode="after_market")
