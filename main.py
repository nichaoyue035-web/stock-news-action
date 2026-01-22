import requests
import time
import random
import os
import datetime
from datetime import timezone, timedelta

# 获取密钥
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 设置北京时区
SHA_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')

def random_wait():
    """随机等待 0-7200秒 (2小时)"""
    # 如果检测到是手动测试运行（GITHUB_EVENT_NAME），则不等待，直接运行
    if os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch':
        print("⚡ 检测到手动触发，跳过等待，立即执行！")
        return

    wait_seconds = random.randint(0, 7200)
    print(f"🕒 计划在 8:00 - 10:00 之间运行。")
    print(f"💤 脚本将睡眠 {wait_seconds} 秒 ({wait_seconds/60:.1f} 分钟)...")
    time.sleep(wait_seconds)
    print("⏰ 睡眠结束，开始干活！")

def get_news():
    print("🔍 正在抓取新浪财经...")
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data['result']['status']['code'] != 0: return []
        
        items = data['result']['data']
        news_list = []
        now = datetime.datetime.now(SHA_TZ)
        one_day_ago = now - timedelta(hours=24)
        
        for item in items:
            pub_time = datetime.datetime.fromtimestamp(int(item['ctime']), SHA_TZ)
            if pub_time < one_day_ago: continue
            
            title = item.get('rich_text', item.get('title', '')).replace('<b>','').replace('</b>','').replace('<font color="red">','').replace('</font>','')
            link = item.get('url', '')
            
            # 简单筛选逻辑：只要最近24小时的前15条
            news_list.append(f"• <a href='{link}'>{title}</a> ({pub_time.strftime('%H:%M')})")
            
        return news_list[:15] # 限制数量
    except Exception as e:
        print(f"❌ 错误: {e}")
        return []

def send_tg(content):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 缺少密钥，无法发送")
        return
    
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}
    data = {
        "chat_id": TG_CHAT_ID,
        "text": content,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    requests.post(url, json=data, headers=headers)

if __name__ == "__main__":
    random_wait()
    news = get_news()
    if news:
        date_str = datetime.datetime.now(SHA_TZ).strftime("%Y-%m-%d")
        msg = f"<b>📅 财经早报 {date_str}</b>\n\n" + "\n\n".join(news)
        send_tg(msg)
        print("✅ 发送成功")
    else:
        print("📭 无数据")
