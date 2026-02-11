import requests
import json
import re
import time
import datetime
import random
from datetime import timedelta
from config import settings
from utils.notifier import log_error, log_info

def get_random_header():
    """生成随机请求头，伪装成浏览器"""
    return {
        "User-Agent": random.choice(settings.USER_AGENTS),
        "Referer": "https://eastmoney.com/"
    }

def get_news(minutes_lookback=None):
    """
    抓取财经快讯
    :param minutes_lookback: 回溯多少分钟内的新闻，None表示24小时
    """
    timestamp = int(time.time() * 1000)
    url = f"{settings.URL_NEWS}?_={timestamp}"
    
    try:
        resp = requests.get(url, headers=get_random_header(), timeout=15)
        content = resp.text.strip()
        
        # 东方财富返回的是非标准JSON，需要截取
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            data = json.loads(content[start_idx : end_idx + 1])
        else:
            return []
        
        items = data.get('LivesList', [])
        valid_news = []
        now = datetime.datetime.now(settings.SHA_TZ)
        # 默认回溯24小时
        delta = timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
        time_threshold = now - delta
        
        for item in items:
            # 时间解析与过滤
            show_time_str = item.get('showtime')
            try:
                news_time = datetime.datetime.strptime(show_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=settings.SHA_TZ)
            except:
                continue
            
            if news_time < time_threshold:
                continue
            
            # 内容清洗
            digest = item.get('digest', '')
            title = item.get('title', '')
            if len(title) < 5: 
                title = digest[:50] + "..." if len(digest) > 50 else digest
            
            # 去除HTML标签
            title = re.sub(r'<[^>]+>', '', title)
            link = item.get('url_unique') or "https://kuaixun.eastmoney.com/"
            
            valid_news.append({
                "title": title, 
                "digest": re.sub(r'<[^>]+>', '', digest), 
                "link": link, 
                "time_str": news_time.strftime('%H:%M'),
                "datetime": news_time 
            })
        return valid_news
    except Exception as e:
        log_error(f"❌ 新闻抓取失败: {e}")
        return []

def get_market_funds():
    """抓取行业资金流向"""
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1", 
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90 t:2",
        "fields": "f12,f14,f2,f3,f62" 
    }
    try:
        resp = requests.get(settings.URL_FUNDS, headers=get_random_header(), params=params, timeout=10)
        data = resp.json().get('data', {}).get('diff', [])
        sectors = []
        for item in data:
            flow = item.get('f62', 0)
            if flow is None: flow = 0
            sectors.append({
                "name": item.get('f14', '未知'),
                "change": f"{item.get('f3', 0)}%",
                "flow": round(flow / 100000000, 2)
            })
        # 按资金流向排序
        sectors.sort(key=lambda x: x['flow'], reverse=True)
        return sectors[:8], sectors[-8:] # 返回前8和后8
    except Exception as e:
        log_error(f"❌ 资金流向获取失败: {e}")
        return [], []

def get_hot_stocks_data():
    """抓取成交额前20的热门股"""
    params = {
        "pn": "1", "pz": "20", "po": "1", "np": "1", 
        "fltt": "2", "invt": "2", "fid": "f6", # 按成交额排序
        "fs": "m:0+t:6,m:0+t:80", # 沪深A股
        "fields": "f12,f14,f3,f6" 
    }
    try:
        resp = requests.get(settings.URL_FUNDS, headers=get_random_header(), params=params, timeout=10)
        data = resp.json().get('data', {}).get('diff', [])
        stock_list = []
        for item in data:
            stock_list.append({
                "name": item['f14'],
                "code": item['f12'],
                "pct": f"{item['f3']}%",
                "amount": f"{round(item['f6']/100000000, 1)}亿"
            })
        return stock_list
    except Exception as e:
        log_error(f"❌ 热门股获取失败: {e}")
        return []



def _normalize_eastmoney_decimal(raw_value, scale=100, digits=2):
    """将东方财富常见的放大整数行情字段还原为小数文本。"""
    if raw_value in (None, '-', ''):
        return '-'

    try:
        value = float(raw_value)
        return f"{value / scale:.{digits}f}"
    except (ValueError, TypeError):
        return str(raw_value)


def get_stock_quote(code):
    """抓取单只股票行情"""
    # 简单的市场判断：6开头是沪市(1)，其他认为是深市(0)
    sec_id = f"1.{code}" if str(code).startswith("6") else f"0.{code}"
    url = f"{settings.URL_QUOTE}?secid={sec_id}&fields=f43,f170,f14"
    try:
        resp = requests.get(url, headers=get_random_header(), timeout=5)
        data = resp.json().get('data', {})
        if not data: return None
        return {
            "name": data.get('f14', '未知'),
            # 东财 f43 常见为放大100倍的最新价（如 1234 -> 12.34）
            "price": _normalize_eastmoney_decimal(data.get('f43'), scale=100, digits=2),
            # 东财 f170 常见为放大100倍的涨跌幅（如 156 -> 1.56）
            "pct": _normalize_eastmoney_decimal(data.get('f170'), scale=100, digits=2)
        }
    except Exception as e:
        log_error(f"❌ 个股行情获取失败 [{code}]: {e}")
        return None
