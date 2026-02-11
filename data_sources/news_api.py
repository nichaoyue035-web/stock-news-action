import requests
import json
import re
import time
import datetime
import random
from datetime import timedelta
from config import SHA_TZ, USER_AGENTS, NEWS_API_URL

def get_news(minutes_lookback=None):
    """
    从东方财富抓取快讯
    """
    timestamp = int(time.time() * 1000)
    # 使用 config 中定义的 URL
    url = f"{NEWS_API_URL}?_={timestamp}"
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://eastmoney.com/"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        content = resp.text.strip()
        
        # 解析 JSON 数据
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            data = json.loads(content[start_idx : end_idx + 1])
        else:
            return []
        
        items = data.get('LivesList', [])
        valid_news = []
        now = datetime.datetime.now(SHA_TZ)
        # 默认看24小时内的新闻
        time_threshold = now - timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
        
        for item in items:
            show_time_str = item.get('showtime')
            try:
                news_time = datetime.datetime.strptime(show_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHA_TZ)
            except:
                continue
            
            if news_time < time_threshold:
                continue
            
            digest = item.get('digest', '')
            title = item.get('title', '')
            # 如果标题太短，用内容截取代替
            if len(title) < 5:
                title = digest[:50] + "..." if len(digest) > 50 else digest
            
            # 清洗 HTML 标签
            title = re.sub(r'<[^>]+>', '', title)
            link = item.get('url_unique') if item.get('url_unique') else "https://kuaixun.eastmoney.com/"
            
            valid_news.append({
                "title": title, 
                "digest": re.sub(r'<[^>]+>', '', digest), 
                "link": link, 
                "time_str": news_time.strftime('%H:%M'),
                "datetime": news_time 
            })
        return valid_news
    except Exception as e:
        print(f"❌ 新闻抓取失败: {e}")
        return []
