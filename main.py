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
    【数据源】东方财富 7x24 (抓取100条)
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
            
            # 获取摘要和标题
            digest = item.get('digest', '')
            title = item.get('title', '')
            
            # 如果标题太短，用摘要补充
            if len(title) < 5:
                title = digest[:50] + "..." if len(digest) > 50 else digest
            
            # 清洗HTML
            title = re.sub(r'<[^>]+>', '', title)
            digest = re.sub(r'<[^>]+>', '', digest) # 清洗摘要
            
            link = item.get('url_unique') if item.get('url_unique') else "https://kuaixun.eastmoney.com/"
            
            valid_news.append({
                "title": title,
                "digest": digest, # 新增：把摘要也存下来给AI看
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
    
    # === 模式 A: 每日早报 (理性策略版) ===
    if mode == "daily":
        print("📝 正在进行策略推演...")
        
        # 投喂更多信息：把新闻的【摘要】也给AI，让它看到细节
        # 选取前 30 条最重要的新闻（数量稍微增加以获取更多上下文）
        news_inputs = []
        for n in news_list[:30]:
            # 格式：[时间] 标题 (详情: 摘要前80字...)
            detail = n['digest'][:80] if n['digest'] else "无详情"
            news_inputs.append(f"- {n['time']} {n['title']} (详情: {detail})")
            
        news_text_block = chr(10).join(news_inputs)

        # ⚡️ 核心 Prompt：去激进化 + 双主线逻辑
        prompt = f"""
        你是一位理性的A股资深策略分析师，擅长从基本面和事件驱动角度挖掘机会。
        这里是过去24小时的快讯：
        {news_text_block}

        请输出一份《今日市场前瞻》，内容要求客观、逻辑清晰，避免使用“最强”、“无敌”等夸张词汇。

        第一部分：【市场情绪定调】
        用 1-2 句话客观评价当前消息面偏暖还是偏冷，并指出核心变量（如美联储、汇率、国内政策）。

        第二部分：【核心机会前瞻】（重点）
        基于消息面，推演今日值得关注的 **1-2 条核心主线**。
        *要求*：
        1. 如果有两条并列的强逻辑（例如“科技”和“消费”都有利好），请列出 **关注方向 A** 和 **关注方向 B**。
        2. 如果只有一条突出的，**不要强行凑数**，只写一条即可。
        
        输出格式：
        📌 **关注方向**：[概念名称]
        💡 **逻辑解析**：[这里稍微多写一点，解释清楚为什么利好，政策背景是什么，资金大概率怎么想]
        🧬 **相关标的**：
        - [股票A]：[简述逻辑，如：行业市占率第一]
        - [股票B]：[简述逻辑]
        （个股推荐保持 2-3 只具有辨识度的）

        (如果有第二个方向，请按同样格式列出；如果没有，则不写)

        不要使用Markdown代码块，保持文字排版整洁。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            final_msg = f"<b>☕️ 市场前瞻 ({current_date})</b>\n\n{summary}\n\n<i>(本内容基于AI分析，仅供参考)</i>"
            send_tg(final_msg)
            
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")
            send_tg(f"⚠️ AI 生成出错。")

    # === 模式 B: 突发监控 (保持灵敏，不变) ===
    elif mode == "monitor":
        print("👮 监控模式...")
        news_titles = [f"{i}. {n['title']}" for i, n in enumerate(news_list[:8])]
        
        prompt = f"""
        你是风控官。审阅快讯：
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
