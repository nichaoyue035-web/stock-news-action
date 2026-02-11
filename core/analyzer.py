import json
import os
import re
from datetime import datetime, timedelta
from config import settings
from utils.notifier import send_tg, log_info, log_error
from utils.ai_client import get_ai_response
from core.data_fetcher import get_news, get_market_funds, get_hot_stocks_data, get_stock_quote

def load_prompts():
    """加载提示词：优先读取本地文件，失败则使用默认配置"""
    try:
        if os.path.exists(settings.PROMPTS_FILE):
            with open(settings.PROMPTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_error(f"⚠️ 提示词文件读取失败: {e}，将使用默认 Prompt")
    return settings.DEFAULT_PROMPTS

def run_recommend():
    """【选股模式】AI 基于热点选股"""
    log_info("启动：AI 选股推荐")
    
    # 1. 获取市场活跃股 (候选池)
    candidates = get_hot_stocks_data()
    if not candidates:
        log_error("❌ 无法获取市场活跃股，选股中止")
        return
    
    candidates_str = "\n".join([f"- {s['name']} (代码:{s['code']}, 涨幅:{s['pct']}, 成交:{s['amount']})" for s in candidates])
    
    # 2. 获取新闻背景
    news = get_news(720) # 过去12小时
    news_txt = "\n".join([f"- {n['title']}" for n in news[:15]])
    
    # 3. 组装 Prompt
    base_prompt = (
        "你是极其理性的量化交易员。请从下方的【候选股票列表】中，挑选唯一一只最符合当前市场热点和新闻面的股票。\n\n"
        f"【候选股票列表】:\n{candidates_str}\n\n"
        f"【近期新闻】:\n{news_txt}\n\n"
        "要求：\n1. 必须从候选列表中选一只，绝对禁止捏造。\n"
        "2. 输出 JSON 格式：{\"name\": \"股票名\", \"code\": \"6位代码\", \"reason\": \"简短理由\"}"
    )
    
    # 4. 调用 AI (低温度，保证理性)
    content = get_ai_response(base_prompt, temperature=0.1)
    if not content: return

    # 5. 解析并验证
    try:
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if not json_match:
            log_error("❌ AI 未输出有效的 JSON 格式")
            return
            
        pick_data = json.loads(json_match.group())
        
        # 二次验真：确保代码存在且能获取行情
        real_quote = get_stock_quote(pick_data['code'])
        if not real_quote:
            log_error(f"❌ 防幻觉拦截：AI 推荐了不存在的股票代码 {pick_data['code']}")
            return
            
        # 6. 保存记忆并通知
        with open(settings.PICK_FILE, "w", encoding="utf-8") as f:
            json.dump(pick_data, f, ensure_ascii=False, indent=2)
            
        send_tg(f"<b>🎯 今日AI精选 (Pro版)</b>\n\n🦄 <b>{pick_data['name']} ({pick_data['code']})</b>\n当前价: {real_quote['price']}\n\n📝 <b>逻辑：</b>\n{pick_data['reason']}")
        log_info(f"✅ 选股完成: {pick_data['name']}")
        
    except Exception as e:
        log_error(f"❌ 选股结果解析失败: {e}")

def run_track():
    """【追踪模式】跟踪已选股票"""
    log_info("启动：个股追踪")
    
    if not os.path.exists(settings.PICK_FILE):
        log_info("⚠️ 没有找到今日选股记录，跳过追踪")
        return
        
    try:
        with open(settings.PICK_FILE, "r", encoding="utf-8") as f:
            pick_data = json.load(f)
        
        quote = get_stock_quote(pick_data['code'])
        if not quote: return

        prompts = load_prompts()
        track_prompt = prompts.get("track", settings.DEFAULT_PROMPTS["track"]).format(
            name=pick_data['name'], code=pick_data['code'], price=quote['price'], pct=quote['pct']
        )
        
        analysis = get_ai_response(track_prompt)
        if not analysis: return
        
        icon = "🔴" if float(quote['pct'].strip('%')) > 0 else "🟢"
        send_tg(f"<b>👀 选股跟踪: {pick_data['name']}</b>\n\n{icon} 现价: {quote['price']} ({quote['pct']}%)\n\n🧠 <b>AI观点：</b>\n{analysis}")
        
    except Exception as e:
        log_error(f"❌ 追踪执行失败: {e}")

def run_analysis(mode):
    """【通用模式】处理早报、资金、监控等"""
    log_info(f"启动：通用分析模式 [{mode}]")
    prompts = load_prompts()
    
    if mode == "funds":
        top_in, top_out = get_market_funds()
        if not top_in: return
        in_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out])
        
        prompt = prompts["funds"].format(in_str=in_str, out_str=out_str)
        content = get_ai_response(prompt)
        if content:
            send_tg(f"<b>💰 主力资金雷达</b>\n\n{content}")

    elif mode == "daily":
        news = get_news(1440) # 24小时
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:30]])
        
        prompt = prompts["daily"].format(news_txt=news_txt)
        content = get_ai_response(prompt)
        if content:
            send_tg(f"<b>🌅 股市全景内参</b>\n\n{content}")

    elif mode == "monitor":
        news = get_news(60) # 1小时
        # 筛选最近25分钟的新闻
        recent_threshold = datetime.now(settings.SHA_TZ) - timedelta(minutes=25)
        fresh_news = [n for n in news if n['datetime'] > recent_threshold]
        if not fresh_news: 
            log_info("暂无最新重要快讯")
            return

        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(fresh_news[:15])]
        prompt = prompts["monitor"].format(news_list="\n".join(news_titles))
        
        content = get_ai_response(prompt)
        if not content: return
        
        # 解析 ALERT 格式
        alerts_buffer = []
        for line in content.split('\n'):
            if "ALERT|" in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    try:
                        idx = int(re.sub(r'\D', '', parts[1]))
                        if idx < len(fresh_news):
                            t = fresh_news[idx]
                            alerts_buffer.append(f"💡 <b>逻辑</b>：{parts[2]}\n📰 <a href='{t['link']}'>{t['title']}</a> ({t['time_str']})")
                    except: pass
        
        if alerts_buffer:
            send_tg("<b>🎯 机会雷达汇总</b>\n\n" + "\n\n〰️〰️〰️〰️〰️\n\n".join(alerts_buffer))

    elif mode in ["periodic", "after_market"]:
        news = get_news(240) # 4小时
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:25]])
        
        prompt = prompts.get(mode, settings.DEFAULT_PROMPTS[mode]).format(news_txt=news_txt)
        title = "🌇 每日复盘" if mode == "after_market" else "🍵 盘中茶歇"
        
        content = get_ai_response(prompt)
        if content:
            send_tg(f"<b>{title}</b>\n\n{content}")
