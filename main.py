import requests
import time
import os
import datetime
import sys
import re
from datetime import timezone, timedelta
from openai import OpenAI

# === 配置区域 ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

def get_news(minutes_lookback=None):
    """
    【数据源升级】使用东方财富 7x24 小时快讯
    """
    # 东方财富的接口，limit=50 表示一次抓50条
    # 这里的 _ 是时间戳防缓存
    timestamp = int(time.time() * 1000)
    url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html?_={timestamp}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://kuaixun.eastmoney.com/"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        # 东方财富有时候返回的不是纯JSON，可能带var xxx=，需要清洗一下
        # 但这个接口通常返回标准JSON，如果有问题需加清洗逻辑
        data = resp.json()
        items = data.get('LivesList', [])
        
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        
        # 确定时间窗口
        if minutes_lookback:
            # 监控模式：稍微放宽一点时间窗口，防止漏抓
            time_threshold = now - timedelta(minutes=minutes_lookback + 2)
        else:
            # 日报模式：24小时
            time_threshold = now - timedelta(hours=24)
        
        for item in items:
            # 东方财富的时间格式通常是 "2024-01-22 10:00:00"
            show_time_str = item.get('showtime')
            try:
                # 它是北京时间，直接解析
                news_time = datetime.datetime.strptime(show_time_str, "%Y-%m-%d %H:%M:%S")
                # 赋予时区信息，否则无法和 now 比较
                news_time = news_time.replace(tzinfo=SHA_TZ)
            except:
                continue

            if news_time < time_threshold:
                continue
            
            # 东方财富的 'digest' 是正文，'title' 是标题
            # 很多快讯没有标题，只有 digest，所以优先用 digest
            content = item.get('digest', '')
            title = item.get('title', '')
            
            # 如果标题太短或为空，就用内容的前30个字当标题
            if len(title) < 5:
                title = content[:50] + "..." if len(content) > 50 else content
            
            # 简单的清洗，去掉HTML标签
            title = re.sub(r'<[^>]+>', '', title)
            
            # 东方财富很多快讯没有独立链接，统一指向快讯首页
            link = "https://kuaixun.eastmoney.com/"
            if item.get('url_unique'):
                link = item.get('url_unique')
            
            valid_news.append({
                "title": title,
                "link": link,
                "time": news_time.strftime('%H:%M')
            })
            
        return valid_news
    except Exception as e:
        print(f"❌ 东方财富抓取错误: {e}")
        return []

def analyze_and_notify(news_list, mode="daily"):
    if not news_list:
        print("📭 时间段内无新闻")
        return

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    
    if mode == "daily":
        # === 日报逻辑不变 ===
        news_titles = [f"- {n['title']}" for n in news_list[:20]] # 东财只有标题比较碎，多给点
        prompt = f"""
        你是金融分析师。请总结以下24小时财经快讯：
        {chr(10).join(news_titles)}
        任务：1.一句话概括情绪 2.三个核心看点 3.利好/利空板块。
        """
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            links_text = "\n".join([f"• {n['title']} ({n['time']})" for n in news_list[:15]])
            send_tg(f"<b>📅 东方财富早报</b>\n\n{summary}\n\n<b>📰 最新资讯：</b>\n{links_text}")
        except Exception as e:
            print(f"AI 错误: {e}")

    elif mode == "monitor":
        # === 监控逻辑 ===
        # 东财消息比较多，只取最新的 6 条给 AI 判断
        news_titles = [f"{i}. {n['title']}" for i, n in enumerate(news_list[:6])]
        
        prompt = f"""
        你是一个极其严格的风控官。请审阅这几条最新财经快讯：
        {chr(10).join(news_titles)}

        请判断其中是否包含【超级重磅】事件。
        标准：只有 央行降息/加息、战争爆发/升级、国家级重磅政策、巨头暴雷/被查、股市崩盘/暴涨 才算。
        普通的财报、盘中异动、分析师观点一律不算。

        如果包含重磅事件，请输出格式：
        ALERT|新闻序号|简短的一句话解读(加emoji)
        
        如果没有，请仅输出：NO
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
                    except:
                        pass
            else:
                print("😴 AI 判断无重大新闻")
                
        except Exception as e:
            print(f"AI 监控出错: {e}")

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    requests.post(url, json=data, headers=headers)

if __name__ == "__main__":
    mode = "daily"
    if len(sys.argv) > 1:
        mode = sys.argv[1]

    print(f"🚀 启动模式: {mode} (源: 东方财富)")
    
    if mode == "daily":
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        # 配合 5分钟的 cron，我们抓取过去 8 分钟的新闻，确保不漏
        news = get_news(minutes_lookback=8)
        analyze_and_notify(news, mode="monitor")
