import json
import os
import re
import csv
from datetime import datetime, timedelta
from config import settings
from utils.notifier import send_tg, log_info, log_error
from utils.ai_client import get_ai_response
from core.data_fetcher import get_news, get_market_funds, get_hot_stocks_data, get_stock_quote

def load_prompts():
    try:
        if os.path.exists(settings.PROMPTS_FILE):
            with open(settings.PROMPTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_error(f"⚠️ 提示词文件读取失败: {e}，将使用默认 Prompt")
    return settings.DEFAULT_PROMPTS

def run_recommend():
    log_info("启动：AI 选股推荐")
    candidates = get_hot_stocks_data()
    if not candidates:
        log_error("❌ 无法获取市场活跃股，选股中止")
        return
    
    candidates_str = "\n".join([f"- {s['name']} (代码:{s['code']}, 涨幅:{s['pct']}, 成交:{s['amount']})" for s in candidates])
    news = get_news(720)
    news_txt = "\n".join([f"- {n['title']}" for n in news[:15]])
    
    base_prompt = (
        "你是极其理性的量化交易员。请从下方的【候选股票列表】中，挑选唯一一只最符合当前市场热点和新闻面的股票。\n\n"
        f"【候选股票列表】:\n{candidates_str}\n\n"
        f"【近期新闻】:\n{news_txt}\n\n"
        "要求：\n1. 必须从候选列表中选一只，绝对禁止捏造。\n"
        "2. 输出 JSON 格式：{\"name\": \"股票名\", \"code\": \"6位代码\", \"reason\": \"简短理由\"}"
    )
    
    content = get_ai_response(base_prompt, temperature=0.1)
    if not content: return

    try:
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if not json_match: return
        pick_data = json.loads(json_match.group())
        real_quote = get_stock_quote(pick_data['code'])
        if not real_quote: return
            
        with open(settings.PICK_FILE, "w", encoding="utf-8") as f:
            json.dump(pick_data, f, ensure_ascii=False, indent=2)
            
        try:
            today_str = datetime.now(settings.SHA_TZ).strftime("%Y-%m-%d")
            file_exists = os.path.isfile(settings.HISTORY_FILE)
            with open(settings.HISTORY_FILE, "a", newline='', encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists: writer.writerow(["Date", "Name", "Code", "Start_Price", "Reason"])
                writer.writerow([today_str, pick_data['name'], pick_data['code'], real_quote['price'], pick_data['reason'].replace("\n", " ")])
        except Exception as e: log_error(f"❌ 历史记录写入失败: {e}")

        send_tg(f"<b>🎯 今日AI精选 (Pro版)</b>\n\n🦄 <b>{pick_data['name']} ({pick_data['code']})</b>\n当前价: {real_quote['price']}\n\n📝 <b>逻辑：</b>\n{pick_data['reason']}")
        
    except Exception as e:
        log_error(f"❌ 选股结果解析失败: {e}")

def run_track():
    log_info("启动：个股追踪")
    if not os.path.exists(settings.PICK_FILE): return
    try:
        with open(settings.PICK_FILE, "r", encoding="utf-8") as f: pick_data = json.load(f)
        quote = get_stock_quote(pick_data['code'])
        if not quote: return
        
        # 简单处理涨跌幅格式
        pct_str = str(quote.get('pct', '0')).replace('%', '')
        try: pct_num = float(pct_str)
        except: pct_num = 0.0

        prompts = load_prompts()
        track_prompt = prompts.get("track", settings.DEFAULT_PROMPTS["track"]).format(
            name=pick_data['name'], code=pick_data['code'], price=quote['price'], pct=quote['pct']
        )
        analysis = get_ai_response(track_prompt)
        if not analysis: return

        icon = "🔴" if pct_num > 0 else "🟢"
        send_tg(f"<b>👀 选股跟踪: {pick_data['name']}</b>\n\n{icon} 现价: {quote['price']} ({quote['pct']}%)\n\n🧠 <b>AI观点：</b>\n{analysis}")
    except Exception as e: log_error(f"❌ 追踪执行失败: {e}")

def run_analysis(mode):
    log_info(f"启动：通用分析模式 [{mode}]")
    prompts = load_prompts()
    
    if mode == "funds":
        top_in, top_out = get_market_funds()
        if not top_in: return
        in_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_in])
        out_str = "\n".join([f"- {s['name']}: {s['flow']}亿 ({s['change']})" for s in top_out])
        content = get_ai_response(prompts["funds"].format(in_str=in_str, out_str=out_str))
        if content: send_tg(f"<b>💰 主力资金雷达</b>\n\n{content}")

    elif mode == "daily":
        news = get_news(1440)
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:30]])
        content = get_ai_response(prompts["daily"].format(news_txt=news_txt))
        if content: send_tg(f"<b>🌅 股市全景内参</b>\n\n{content}")

    elif mode == "monitor":
        # === 监控模式：逻辑分流 ===
        news = get_news(90)
        now = datetime.now(settings.SHA_TZ)
        strict_threshold = now - timedelta(minutes=15)
        soft_threshold = now - timedelta(minutes=30)
        high_impact_keywords = ["涨停", "跌停", "停牌", "复牌", "业绩", "并购", "重组", "回购", "增持", "减持", "政策", "降息", "AI", "算力", "芯片"]

        fresh_news = []
        for n in news:
            if n['datetime'] >= strict_threshold:
                fresh_news.append(n)
            elif n['datetime'] >= soft_threshold:
                if any(k in f"{n['title']} {n['digest']}" for k in high_impact_keywords):
                    fresh_news.append(n)

        if not fresh_news: return

        dedup_news = []
        seen = set()
        for n in fresh_news:
            if n['title'] not in seen:
                seen.add(n['title'])
                dedup_news.append(n)

        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(dedup_news[:12])]
        content = get_ai_response(prompts["monitor"].format(news_list="\n".join(news_titles)))
        if not content: return

        alerts_buffer = []
        for line in content.split("\n"):
            if "ALERT|" in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    try:
                        idx = int(re.sub(r"\D", "", parts[1]))
                        if idx < len(dedup_news):
                            t = dedup_news[idx]
                            alerts_buffer.append(f"💡 <b>逻辑</b>：{parts[2]}\n📰 <a href='{t['link']}'>{t['title']}</a> ({t['time_str']})")
                    except: continue

        if alerts_buffer:
            msg = "<b>🎯 机会雷达汇总</b>\n\n" + "\n\n〰️〰️〰️〰️〰️\n\n".join(alerts_buffer[:3])
            # 🔥 关键修改：使用监控机器人配置发送
            send_tg(
                msg, 
                token=settings.TG_BOT_TOKEN_MONITOR, 
                chat_id=settings.TG_CHAT_ID_MONITOR
            )
            log_info("✅ 监控消息已发送至副频道")

    elif mode == "global":
        # 抓取过去 3 小时 (180分钟) 的数据
        news = get_news(180) 
        if not news: return
        
        # 提取前 80 条新闻，为 DeepSeek 提供足够的宏观样本池
        news_txt = "\n".join([f"- {n['title']} (详情:{n['digest'][:40]})" for n in news[:80]])
        
        prompt = prompts.get("global", settings.DEFAULT_PROMPTS["global"]).format(news_txt=news_txt)
        content = get_ai_response(prompt)
        
        if content and "无重大事件" not in content:
            # 🚨 必须使用 MONITOR 的配置，物理隔离发送到消息雷达频道
            send_tg(
                f"<b>🌐 国际宏观与板块雷达 (3H)</b>\n\n{content}", 
                token=settings.TG_BOT_TOKEN_MONITOR, 
                chat_id=settings.TG_CHAT_ID_MONITOR
            )
            log_info("✅ 宏观雷达已发送")

    elif mode in ["periodic", "after_market"]:
        news = get_news(240)
        if not news: return
        news_txt = "\n".join([f"- {n['title']}" for n in news[:25]])
        title = "🌇 每日复盘" if mode == "after_market" else "🍵 盘中茶歇"
        content = get_ai_response(prompts.get(mode, settings.DEFAULT_PROMPTS[mode]).format(news_txt=news_txt))
        if content: send_tg(f"<b>{title}</b>\n\n{content}")

def run_review():
    if not os.path.exists(settings.HISTORY_FILE): return
    try:
        with open(settings.HISTORY_FILE, "r", encoding="utf-8") as f: rows = list(csv.DictReader(f))
        recent_rows = rows[-10:] if len(rows) > 10 else rows
        details = []
        total_count = 0
        win_count = 0
        total_profit = 0.0

        for row in recent_rows:
            curr_quote = get_stock_quote(row['Code'])
            if not curr_quote: continue
            try:
                start = float(row['Start_Price'])
                curr = float(curr_quote['price'])
                pct = (curr - start) / start * 100
                total_count += 1
                total_profit += pct
                if pct > 0: win_count += 1
                icon = "🔴" if pct > 0 else "🟢"
                details.append(f"{icon} <b>{row['Name']}</b>: <b>{pct:+.2f}%</b>")
            except: continue

        if total_count == 0: return
        win_rate = (win_count / total_count) * 100
        avg_profit = total_profit / total_count
        send_tg(f"<b>📊 AI 战绩周报</b>\n\n🏆 <b>胜率: {win_rate:.0f}%</b>\n💰 <b>平均收益: {avg_profit:+.2f}%</b>\n------------------\n" + "\n".join(details))
    except Exception as e: log_error(f"复盘失败: {e}")
