import requests
import time
import os
import datetime
import sys
import re
import json  # 👈 新增了这个库用来手动解析
from datetime import timezone, timedelta
from openai import OpenAI

# === 1. 配置区域 ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# 设置北京时区 (UTC+8)
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

def get_news(minutes_lookback=None):
    """
    【数据源】使用东方财富 7x24 小时快讯
    """
    timestamp = int(time.time() * 1000)
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html?_={timestamp}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://kuaixun.eastmoney.com/",
        "Accept": "*/*"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        
        # === 👇 核心修复逻辑在这里 👇 ===
        # 1. 获取原始文本
        content = resp.text.strip()
        
        # 2. 如果是 var xxx = {...} 格式，进行清洗
        if content.startswith("var "):
            # 找到第一个等号，取等号后面的内容
            content = content.split("=", 1)[1].strip()
            # 去掉末尾的分号
            if content.endswith(";"):
                content = content[:-1]
        
        # 3. 手动解析 JSON
        data = json.loads(content)
        items = data.get('LivesList', [])
        # ================================
        
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        
        # 确定筛选的时间范围
        if minutes_lookback:
            time_threshold = now - timedelta(minutes=minutes_lookback + 2)
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
        # 如果出错，打印前100个字符看看服务器到底回了什么（方便调试）
        try:
            print(f"📡 服务器响应内容: {resp.text[:100]}")
        except:
            pass
        return []

def analyze_and_notify(news_list, mode="daily"):
    if not news_list:
        print("📭 当前时间段内无新闻")
        return

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    
    # === 模式 A: 每日早报 ===
    if mode == "daily":
        print("📝 正在生成早报...")
        news_titles = [f"- {n['title']}" for n in news_list[:20]]
        
        prompt = f"""
        你是华尔街资深交易员。请根据以下中国财经快讯写一份简报：
        {chr(10).join(news_titles)}
        
        要求：
        1. 用【一句话】概括当前市场核心情绪。
        2. 列出 3 个最重要的市场信号（加 emoji）。
        3. 如果有明确的利好/利空板块，直接点名。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            links_text = "\n".join([f"• {n['title']} ({n['time']})" for n in news_list[:15]])
            final_msg = f"<b>📅 东方财富早报</b>\n\n{summary}\n\n<b>📰 24h 资讯精选：</b>\n{links_text}"
            send_tg(final_msg)
            
        except Exception as e:
            print(f"❌ AI 生成早报失败: {e}")
            send_tg(f"<b>📅 东方财富早报 (AI暂不可用)</b>\n\n" + "\n".join([f"• {n['title']}" for n in news_list[:15]]))

    # === 模式 B: 突发监控 ===
    elif mode == "monitor":
        print("👮 正在进行风险监控...")
        news_titles = [f"{i}. {n['title']}" for i, n in enumerate(news_list[:6])]
        
        prompt = f"""
        你是一个极其严格的风控官。请审阅这几条最新快讯：
        {chr(10).join(news_titles)}

        请判断其中是否包含【超级重磅】事件。
        【判断标准】：
        - 必须是：央行降息/加息、战争爆发、国家级重磅政策、巨头(腾讯/阿里/苹果)暴雷或被查、股市崩盘。
        - 排除：普通财报、股价小幅波动、分析师观点、行业小新闻。

        【输出格式】：
        如果包含重磅事件，输出：ALERT|新闻序号|一句话犀利解读
        如果没有，仅输出：NO
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
                            f"⏰ 时间: {target_news['time']} (来源: 东方财富)"
                        )
                        send_tg(msg)
                        print("✅ 已发送突发警报")
                    except:
                        pass
            else:
                print("😴 AI 判断无重大风险")
                
        except Exception as e:
            print(f"❌ AI 监控模式出错: {e}")

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 未配置 Telegram 密钥")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(url, json=data, headers=headers, timeout=10)
    except Exception as e:
        print(f"❌ TG 推送网络错误: {e}")

if __name__ == "__main__":
    mode = "daily"
    if len(sys.argv) > 1:
        mode = sys.argv[1]

    print(f"🚀 启动脚本 | 模式: {mode} | 数据源: 东方财富")
    
    if mode == "daily":
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        news = get_news(minutes_lookback=25)
        analyze_and_notify(news, mode="monitor")
