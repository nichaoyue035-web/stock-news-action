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
            # 监控模式：最近 x 分钟
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
    
    # === 模式 A: 每日早报 (全面总结 + 核心主线 + 遗珠拾贝) ===
    if mode == "daily":
        print("📝 正在生成全景早报...")
        
        # 投喂前 40 条最有价值的新闻（增加数量以确保全面）
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

        【第二部分：其他高价值情报】(查漏补缺，非常重要)
        除了上述主线外，**务必列出 3-5 条** 对个股或板块有**直接利好/利空**的独立消息。
        *筛选标准*：
        - 某行业突发重磅利好（但未形成主线）。
        - 某知名公司重大资产重组、业绩超预期或大订单。
        - 关键经济数据发布。
        *格式*：
        🔥 **[事件名]**：[一句话解读影响]

        【第三部分：市场情绪风向】
        用一句话总结今日多空情绪（激进/稳健/观望）。

        要求：内容务实、全面，不要漏掉重要信息，也不要堆砌垃圾信息。
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

    # === 模式 B: 突发监控 (高灵敏度版) ===
    elif mode == "monitor":
        print("⚡️ 极速监控模式...")
        # 监控只看最新的 8 条
        news_titles = [f"{i}. {n['title']} (详情:{n['digest'][:50]})" for i, n in enumerate(news_list[:8])]
        
        # ⚡️ 核心修改：大幅降低报警门槛，强调“即时性”和“股价波动”
        prompt = f"""
        你是一个毫秒级的短线交易雷达。请扫描最新快讯：
        {chr(10).join(news_titles)}

        【任务】：
        判断是否有**立刻能引起股价明显波动**的消息。
        
        【判定标准（只要满足其一即报警）】：
        1. ✅ **突发政策**：部委/地方政府刚刚发布的新规（如低空、地产、半导体）。
        2. ✅ **盘中异动**：某板块突然拉升/跳水的解释性消息。
        3. ✅ **公司大新闻**：业绩预告、中标大单、资产重组、被立案调查。
        4. ✅ **知名小作文**：虽然未证实但市场关注度极高的传闻。
        
        (注意：不要只盯着央行降息这种核弹消息，任何能带来交易机会的消息都要报！)

        【输出格式】：
        ALERT|新闻序号|一句话交易提示(利好谁/利空谁/什么题材)
        
        如果没有，输出：NO
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
                        
                        # 监控消息增加 emoji 提醒，体现紧迫感
                        msg = (
                            f"<b>⚡️ 盘中异动提醒！</b>\n\n"
                            f"💡 {comment}\n\n"
                            f"📰 <a href='{target_news['link']}'>{target_news['title']}</a>\n"
                            f"⏰ {target_news['time']}"
                        )
                        send_tg(msg)
                    except: pass
            else:
                print("😴 无波动机会")
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
    mode = "daily"
    if len(sys.argv) > 1: mode = sys.argv[1]
    
    print(f"🚀 启动 | 模式: {mode}")
    
    if mode == "daily":
        news = get_news(minutes_lookback=None)
        analyze_and_notify(news, mode="daily")
    elif mode == "monitor":
        news = get_news(minutes_lookback=25)
        analyze_and_notify(news, mode="monitor")
