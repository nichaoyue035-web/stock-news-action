import requests
import random
from config import USER_AGENTS, MARKET_API_URL, STOCK_DETAIL_URL

def get_random_header():
    """获取随机请求头"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://eastmoney.com/"
    }

def get_market_funds():
    """获取行业资金流向数据"""
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1", 
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90 t:2",
        "fields": "f12,f14,f2,f3,f62" 
    }
    try:
        resp = requests.get(MARKET_API_URL, headers=get_random_header(), params=params, timeout=10)
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
        sectors.sort(key=lambda x: x['flow'], reverse=True)
        return sectors[:8], sectors[-8:]
    except:
        return [], []

def get_hot_stocks_data():
    """获取市场成交额前20的活跃股 (防幻觉用)"""
    params = {
        "pn": "1", "pz": "20", "po": "1", "np": "1", 
        "fltt": "2", "invt": "2", "fid": "f6", 
        "fs": "m:0+t:6,m:0+t:80", 
        "fields": "f12,f14,f3,f6" 
    }
    try:
        resp = requests.get(MARKET_API_URL, headers=get_random_header(), params=params, timeout=10)
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
    except:
        return []

def get_stock_quote(code):
    """获取单只个股实时行情"""
    sec_id = f"1.{code}" if str(code).startswith("6") else f"0.{code}"
    url = f"{STOCK_DETAIL_URL}?secid={sec_id}&fields=f43,f170,f14"
    try:
        resp = requests.get(url, headers=get_random_header(), timeout=5)
        data = resp.json().get('data', {})
        if not data: return None
        return {
            "name": data.get('f14', '未知'),
            "price": data.get('f43', '-'),
            "pct": data.get('f170', '-')
        }
    except:
        return None
