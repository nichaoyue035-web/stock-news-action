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
            # 监控模式/周期模式：最近 x 分钟
            time_threshold = now - timedelta(minutes=minutes_lookback + 5)
        else:
            # 日报模式：过去 24 小时
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
            digest = re.sub(r'<[^>]+>', '', digest)
            
            link = item.get('url_unique') if item.get('url_unique') else "https://kuaixun.eastmoney.com/"
            
            valid_news.append({
                "title": title,
                "digest": digest,
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
    
    # === 模式 A: 每日早报 (全面总结) ===
    if mode == "daily":
        print("📝 正在生成全景早报...")
        
        # 投喂前 40 条
        news_inputs = []
        for n in news_list[:40]:
            detail = n['digest'][:100] if n['digest'] else "无详情"
            news_inputs.append(f"- [{n['time']}] {n['title']} (内容: {detail})")
            
        news_text_block = chr(10).join(news_inputs)

        prompt = f"""
        你是一位视野宏大的A股投资总监。请阅读过去24小时的新闻：
        {news_text_block}

        请制作一份**高价值**的《今日盘前内参》。
        
        【第一部分：核心主线推演】(最重要，定方向)
        从杂乱信息中提炼出 **1条** 最具爆发力的炒作主线（只写最强的1条）。
        - 🎯 **主线题材**：[名称]
        - 💡 **爆发逻辑**：[结合政策/事件/资金面深度解析]
        - 🧬 **龙头前瞻**：[推荐2只最核心个股，简述理由]

        【第二部分：其他高价值情报】
        务必列出 3-5 条对个股或板块有**直接利好/利空**的独立消息。
        🔥 **[事件名]**：[一句话解读影响]

        【第三部分：市场情绪风向】
        用一句话总结今日多空情绪（激进/稳健/观望）。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            
            current_date = datetime.datetime.now(SHA_TZ).strftime("%m月%d日")
            final_msg = f"<b>🌅 股市全景内参 ({current_date})</b>\n\n{summary}\n\n<i>(AI 辅助决策，仅供参考)</i>"
            send_tg(final_msg)
            
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")
            send_tg(f"⚠️ 早报生成出错。")

    # === 模式 B: 周期性盘中快报 (每几小时发一次) ===
    elif mode == "periodic":
        print("🕒 正在生成时段简报...")
        
        if len(news_list) < 5:
            print(f"😴 新闻只有 {len(news_list)} 条，跳过不发。")
            return

        news_inputs = []
        for n in news_list[:20]:
            detail = n['digest'][:80] if n['digest'] else "无详情"
            news_inputs.append(f"- [{n['time']}] {n['title']}")
        news_text_block = chr(10).join(news_inputs)

        prompt = f"""
        你是一位即时财经编辑。这是过去几小时的快讯：
        {news_text_block}

        请快速总结一份《盘中时段简报》。
        1. 直接列出 2-3 个值得关注的重点事件或板块异动。
        2. 若无重要事，就总结为“消息面平静”。
        3. 格式短小精悍。
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            summary = resp.choices[0].message.content
            current_time = datetime.datetime.now(SHA_TZ).strftime("%H:%M")
            final_msg = f"<b>🍵 盘中茶歇 ({current_time})</b>\n\n{summary}"
            send_tg(final_msg)
        except Exception as e:
            print(f"❌ AI 生成失败: {e}")

    # === 模式 C: 突发监控 (均衡灵敏度) ===
    elif mode == "monitor":
        print("⚡️ 监控模式 (均衡灵敏度)...")
        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:60]})" for i, n in enumerate(news_list[:15])]
        
        prompt = f"""
        你是一个专业的A股短线交易员。请扫描最新快讯：
        {chr(10).join(news_titles)}

        【任务】：
        筛选出具有**短线交易价值**的消息。
        
        【筛选标准（平衡策略）】：
        1. ✅ **保留**：业绩预告/快报、中标合同、资产重组、监管立案、行业重磅政策、产品涨价、知名市场传闻。
        2. ❌ **过滤**：常规行政公告（如收到辞职信、召开会议通知但无内容、微不足道的质押、常规担保）。
        3. **核心判断**：这条消息能否让看到的人想去买或卖股票？如果是，就报警；如果看完内心毫无波动，就忽略。

        【输出格式】：
        ALERT|新闻序号|简短交易提示(利好/利空/题材)
        
        如果没有值得交易的消息，输出：NO
        """
        
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}], stream=False
            )
            ai_reply = resp.choices[0].message.content.strip()
            
            if "ALERT|" in ai_reply:
                lines = ai_reply.split('\n')
                alert_triggered = False
                
                for line in lines:
                    if "ALERT|" in line:
                        parts = line.split("|") 
                        if len(parts) >= 3:
                            try:
                                index_str = re.sub(r'\D', '', parts[1])
                                index = int(index_str)
                                comment = parts[2]
                                
                                if index < len(news_list):
                                    target_news = news_list[index]
                                    
                                    msg = (
                                        f"<b>🚨 机会雷达</b>\n\n"
                                        f"💡 {comment}\n\n"
                                        f"📰 <a href='{target_news['link']}'>{target_news['title']}</a>\n"
                                        f"⏰ {target_news['time']}"
                                    )
                                    send_tg(msg)
                                    alert_triggered = True
                            except Exception as inner_e:
                                print(f"解析错误: {inner_e}")
                
                if not alert_triggered:
                    print("AI 返回了 ALERT 但解析没成功，或者格式不对。")
            else:
                print("😴 无交易机会")
                
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
    # 👇 核心修改：程序一启动就先发个通知
    send_tg("🚀 收到 Push！机器人代码更新并开始运行测试...")

    mode = "daily"
    if len(sys.argv) > 1: mode = sys.argv[1]
    
    print(f"🚀 启动 | 模式: {mode}")
    
    if mode == "daily":
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        # 监控过去 25 分钟
        news = get_news(minutes_lookback=25)
        analyze_and_notify(news, mode="monitor")
    elif mode == "periodic":
        # 周期总结抓取过去 4 小时
        news = get_news(minutes_lookback=240)
        analyze_and_notify(news, mode="periodic")
