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
    【数据源】使用东方财富 7x24 小时快讯
    """
    timestamp = int(time.time() * 1000)
    
    # ⚡️ 核心修改 1：把获取数量从 50 改为 100，防止漏掉被刷下去的重磅新闻
    # URL 里的 _100_ 代表 pageSize
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_100_1_.html?_={timestamp}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://kuaixun.eastmoney.com/",
        "Accept": "*/*"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        content = resp.text.strip()
        
        # 清洗 var xxx = {...}
        if content.startswith("var "):
            content = content.split("=", 1)[1].strip()
            if content.endswith(";"):
                content = content[:-1]
        
        data = json.loads(content)
        items = data.get('LivesList', [])
        
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        
        # 确定筛选范围
        if minutes_lookback:
            # 监控模式：最近 x 分钟
            time_threshold = now - timedelta(minutes=minutes_lookback + 5)
        else:
            # 日报模式：严格的过去 24 小时
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
            
            content_text = item.get('digest', '')
            title = item.get('title', '')
            if len(title) < 5:
                title = content_text[:50] + "..." if len(content_text) > 50 else content_text
            
            title = re.sub(r'<[^>]+>', '', title)
            link = item.get('url_unique') if item.get('url_unique') else "https://kuaixun.eastmoney.com/"
            
            valid_news.append({
                "title": title,
                "link": link,
                "time": news_time.strftime('%H:%M') # 只留时分
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
    
    # === 模式 A: 每日早报 (深度筛选版) ===
    if mode == "daily":
        print("📝 正在生成深度早报...")
        
        # 把抓到的所有新闻标题都给 AI（只要不超 Token，越多越好，让 AI 去挑）
        # 我们这里把 100 条里符合时间的都丢进去，大概率不会超 DeepSeek 的上下文
        news_inputs = [f"- {n['time']} {n['title']}" for n in news_list]
        news_text_block = chr(10).join(news_inputs)

        # ⚡️ 核心修改 2：使用更强的“策略分析师”提示词
        prompt = f"""
        你是一位极其严格的A股首席策略分析师。你的客户是专业的基金经理。
        这里是过去24小时的快讯列表：
        
        {news_text_block}

        【任务目标】：
        请从上述杂乱的信息中，**只筛选出**对今日A股走势有【实质性影响】的消息。
        
        【筛选标准（非常严格）】：
        1. ✅ **宏观政策**：央行（降准/降息/MLF）、国务院、发改委发布的重磅文件。
        2. ✅ **核心数据**：GDP、CPI、PPI、社融、PMI数据超预期/不及预期。
        3. ✅ **行业巨震**：牵扯到万亿市值板块（如新能源、白酒、半导体、券商）的重大利好/利空。
        4. ✅ **外部冲击**：美联储决议、汇率剧烈波动、地缘政治大事件。
        
        ❌ **坚决剔除**：
        - 个股的小道消息或普通财报（除非是茅台、宁德时代这种风向标）。
        - 分析师的口水话、普通的盘中异动播报。
        - 没有任何增量信息的车轱辘话。

        【输出格式】：
        请生成一份《核心内参》，结构如下：
        
        🌐 **市场情绪定调**：(用一句话判断今日是 乐观/谨慎/恐慌，并说明核心理由)
        
        🔥 **必读核心事件**：
        (这里不限制数量，有几条真正的大事就写几条。按影响力排序。如果没有大事，就写“今日无影响趋势的重大消息”。)
        1. [事件名称] + 深度解读（一针见血指出它利好什么板块，或者利空什么）
        2. ...
        
        📊 **板块资金雷达**：
        (基于消息判断，今日哪些板块可能成为风口？哪些需要避险？)
        
        (注意：直接输出内容，不要使用Markdown代码块，保持排版简洁清晰)
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            
            # 发送早报（不再附带长长的新闻流水账链接，只看分析核心）
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            final_msg = f"<b>📅 股市核心内参 ({current_date})</b>\n\n{summary}\n\n<i>(由 AI 剔除 90% 无效噪音，仅保留关键信息)</i>"
            send_tg(final_msg)
            
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")
            send_tg(f"⚠️ AI 罢工了，请检查日志。")

    # === 模式 B: 突发监控 (逻辑保持不变，依然灵敏) ===
    elif mode == "monitor":
        print("👮 监控模式...")
        # 监控只看最新的 8 条
        news_titles = [f"{i}. {n['title']}" for i, n in enumerate(news_list[:8])]
        
        prompt = f"""
        你是风控官。审阅最新快讯：
        {chr(10).join(news_titles)}

        判断是否包含【导致股市瞬间变盘】的超级重磅事件。
        标准：战争、央行大动作、国家级政策、巨头暴雷。
        
        有则输出：ALERT|新闻序号|一句话解读
        无则输出：NO
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            ai_reply = resp.choices[0].message.content.strip()
            
            if "ALERT|" in ai_reply:
                parts = ai_reply.split("|") 
                if len(parts) >= 3:
                    try:
                        index = int(parts[1])
                        comment = parts[2]
                        target_news = news_list[index]
                        msg = (
                            f"<b>🚨 突发重大消息！</b>\n\n"
                            f"{comment}\n\n"
                            f"📰 {target_news['title']}\n"
                            f"⏰ {target_news['time']}"
                        )
                        send_tg(msg)
                    except: pass
            else:
                print("😴 无重磅消息")
        except Exception as e:
            print(f"AI 监控出错: {e}")

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: requests.post(url, json=data, headers=headers, timeout=10)
    except: pass

if __name__ == "__main__":
    mode = "daily"
    if len(sys.argv) > 1: mode = sys.argv[1]
    
    print(f"🚀 启动 | 模式: {mode}")
    
    if mode == "daily":
        # 日报抓取更多数据给 AI 筛选
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        # 监控抓取最近 25 分钟
        news = get_news(minutes_lookback=25)
        analyze_and_notify(news, mode="monitor")
