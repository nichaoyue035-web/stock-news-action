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
    【数据源】使用东方财富 7x24 小时快讯 (抓取100条)
    """
    timestamp = int(time.time() * 1000)
    # 抓取 100 条，确保覆盖面
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
            # 日报模式：过去 24 小时
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
    
    # === 模式 A: 每日早报 (策略推演版) ===
    if mode == "daily":
        print("📝 正在进行主线推演...")
        
        # 提取新闻文本
        news_inputs = [f"- {n['time']} {n['title']}" for n in news_list]
        news_text_block = chr(10).join(news_inputs)

        # ⚡️ 核心 Prompt：增加了【资金进攻推演】部分
        prompt = f"""
        你是一位实战派A股游资大佬，擅长捕捉短线题材和龙头股。
        这里是过去24小时的快讯：
        {news_text_block}

        请输出一份《今日操盘内参》，分为两部分。

        第一部分：【核心大势】
        1. 用一句话定调今日情绪（进攻/防守/震荡）。
        2. 提炼 1-2 个影响最大的宏观或行业大事件（剔除废话）。

        第二部分：【资金进攻推演】（这是重点！）
        基于上述消息，找出今天最可能爆发的 **1 条炒作主线**。
        必须严格按照以下格式输出：

        🎯 **最强主线**：[概念名称，如：低空经济/华为海思]
        💡 **炒作逻辑**：[一句话解释为什么今天资金会去这里]
        🔥 **相关个股**：
        - [股票A]：[入选理由，如：板块龙头/中标大单]
        - [股票B]：[入选理由，如：弹性标的/技术突破]
        （注意：个股只推荐 2-3 只最辨识度的，不要多，宁缺毋滥）

        如果今天没有明确主线，请直说“今日无明显题材，建议空仓”。
        不要使用Markdown代码块，直接输出文字。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            
            # 生成日期
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            final_msg = f"<b>📈 游资内参 ({current_date})</b>\n\n{summary}\n\n<i>(⚠️ 机器推演仅供参考，不构成投资建议)</i>"
            send_tg(final_msg)
            
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")
            send_tg(f"⚠️ AI 罢工了，请检查日志。")

    # === 模式 B: 突发监控 (保持不变) ===
    elif mode == "monitor":
        print("👮 监控模式...")
        news_titles = [f"{i}. {n['title']}" for i, n in enumerate(news_list[:8])]
        
        prompt = f"""
        你是风控官。审阅最新快讯：
        {chr(10).join(news_titles)}
        判断是否包含【超级重磅】事件（央行动作、战争、国家级政策、巨头暴雷）。
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
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        news = get_news(minutes_lookback=25)
        analyze_and_notify(news, mode="monitor")
