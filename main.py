import requests
import time
import os
import datetime
import sys
from datetime import timezone, timedelta
from openai import OpenAI

# === 配置区域 ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

def get_news(minutes_lookback=None):
    """
    minutes_lookback: 如果设置了数字，只抓取最近 x 分钟的新闻（用于突发监控）
    否则抓取 24 小时（用于日报）
    """
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        items = data['result']['data']
        
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        
        # 确定时间窗口
        if minutes_lookback:
            time_threshold = now - timedelta(minutes=minutes_lookback)
        else:
            time_threshold = now - timedelta(hours=24)
        
        for item in items:
            pub_time = datetime.datetime.fromtimestamp(int(item['ctime']), SHA_TZ)
            if pub_time < time_threshold: continue
            
            title = item.get('rich_text', item.get('title', '')).replace('<b>','').replace('</b>','').replace('<font color="red">','').replace('</font>','')
            link = item.get('url', '')
            
            valid_news.append({
                "title": title,
                "link": link,
                "time": pub_time.strftime('%H:%M')
            })
            
        return valid_news
    except Exception as e:
        print(f"❌ 抓取错误: {e}")
        return []

def analyze_and_notify(news_list, mode="daily"):
    if not news_list:
        print("📭 时间段内无新闻")
        return

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    
    # === 模式 A: 每日早报 (总结所有) ===
    if mode == "daily":
        news_titles = [f"- {n['title']}" for n in news_list[:15]]
        prompt = f"""
        你是金融分析师。请总结以下24小时财经新闻：
        {chr(10).join(news_titles)}
        任务：1.一句话概括情绪 2.三个核心看点 3.利好/利空板块。
        直接输出结果，不要废话。
        """
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            
            # 格式化链接列表
            links_text = "\n".join([f"• <a href='{n['link']}'>{n['title']}</a> ({n['time']})" for n in news_list[:15]])
            
            send_tg(f"<b>📅 财经早报</b>\n\n{summary}\n\n<b>📰 消息源：</b>\n{links_text}")
        except Exception as e:
            print(f"AI 错误: {e}")

    # === 模式 B: 突发监控 (只找大事) ===
    elif mode == "monitor":
        # 如果新闻太多，只看最新的5条，避免 AI 晕
        news_titles = [f"{i}. {n['title']}" for i, n in enumerate(news_list[:5])]
        
        prompt = f"""
        你是一个极其严格的风控官。请审阅这几条最新发生的财经新闻：
        {chr(10).join(news_titles)}

        请判断其中是否包含【超级重磅】事件。
        标准：只有 央行降息/加息、战争爆发、国家级政策发布、巨头(如苹果/腾讯)暴雷、股市崩盘/暴涨 才算。
        普通的财报、股价波动、小道消息一律不算。

        如果包含重磅事件，请输出格式：
        ALERT|新闻序号|简短的一句话解读(加emoji)
        
        如果没有重磅事件，请仅输出：NO
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            ai_reply = resp.choices[0].message.content.strip()
            
            if "ALERT|" in ai_reply:
                # 解析 AI 返回的结果
                parts = ai_reply.split("|") # ALERT|1|💥 央行宣布降准！
                if len(parts) >= 3:
                    try:
                        index = int(parts[1])
                        comment = parts[2]
                        target_news = news_list[index]
                        
                        msg = (
                            f"<b>🚨 突发重大消息！</b>\n\n"
                            f"{comment}\n\n"
                            f"📰 <a href='{target_news['link']}'>{target_news['title']}</a>\n"
                            f"⏰ 时间: {target_news['time']}"
                        )
                        send_tg(msg)
                    except:
                        pass
            else:
                print("😴 AI 判断无重大新闻，继续潜伏。")
                
        except Exception as e:
            print(f"AI 监控出错: {e}")

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {"chat_id": TG_CHAT_ID, "text": content, "parse_mode": "HTML", "disable_web_page_preview": True}
    requests.post(url, json=data, headers=headers)

if __name__ == "__main__":
    # 从命令行参数读取模式，默认为 daily
    mode = "daily"
    if len(sys.argv) > 1:
        mode = sys.argv[1]

    print(f"🚀 启动模式: {mode}")
    
    if mode == "daily":
        # 日报模式：看24小时
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        # 监控模式：只看最近 25 分钟 (配合 cron 20分钟一次，留5分钟余量)
        news = get_news(minutes_lookback=25)
        analyze_and_notify(news, mode="monitor")
