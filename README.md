# stock-news-action

## 1. 项目简介

`stock-news-action` 是一个面向个人使用的投资信息流自动化项目。它可以由 GitHub Actions 定时触发，也可以在本地手动运行；核心流程是用 Python 抓取 A 股市场新闻、行业资金流、热门股、海外 RSS 新闻，再调用 DeepSeek 进行摘要、翻译和分析，最后通过 Telegram Bot 推送给用户。

这个项目更适合作为：

- 个人投资信息流压缩器
- 每日市场雷达
- 新闻与资金面的辅助整理工具
- 盘前、盘中、盘后信息提醒助手

请注意：

- 本项目不是自动交易系统。
- 本项目不会自动下单，也不应该被理解为“自动炒股机器人”。
- 本项目输出不构成投资建议。
- AI 生成内容只能作为信息参考，不能直接作为买卖依据。

## 2. 核心功能

- **A 股市场早报**：抓取近期市场新闻，并生成盘前内参。
- **热门新闻抓取**：从东方财富快讯等来源抓取财经新闻。
- **东方财富数据源**：使用东方财富接口获取新闻、行业资金流、热门股票和个股行情。
- **海外 RSS 新闻源**：默认支持海外 RSS 信息源，并可通过环境变量替换或追加。
- **非中文新闻自动翻译**：外部 RSS 新闻会先由 DeepSeek 判断语言，非中文内容会尝试翻译为简体中文。
- **Telegram 推送**：将分析结果推送到主频道或监控频道。
- **AI 每日股票观察 / `recommend` 模式**：从热门股和近期新闻中生成一个观察标的，并保存记录。
- **历史记录保存**：`recommend` 模式会写入 `stock_pick.json` 和 `history.csv`，供后续跟踪和复盘使用。
- **盘中与海外监控**：支持 `monitor`、`periodic`、`global` 等模式，用于盘中机会雷达、茶歇简报和国际宏观事件监控。
- **复盘与跟踪**：支持 `after_market`、`track`、`review` 等模式，用于盘后复盘、个股跟踪和历史表现回看。

## 3. 工作流程

1. **GitHub Actions 或本地命令触发**：按计划任务或手动执行 `python main.py <mode>`。
2. **Python 主程序读取运行模式**：`main.py` 根据命令行参数分发到不同模式。
3. **抓取市场数据和新闻**：根据模式调用新闻、资金流、热门股、行情或 RSS 数据源。
4. **按模式生成摘要 / 翻译 / 分析**：需要 AI 的模式会根据 `prompts.json` 或默认提示词调用 DeepSeek；实时 `monitor` 走确定性规则，不等待 AI。
5. **Telegram 推送**：通过 Telegram Bot 发送到主频道或监控频道。
6. **部分模式写入历史记录**：例如 `recommend` 会保存到 `stock_pick.json` 和 `history.csv`，`review` 会读取历史记录做表现回看。

## 4. 运行模式说明

以下模式由 `main.py` 分发执行。不同模式依赖的数据源和 Telegram 频道不同，具体效果取决于数据接口、DeepSeek 返回结果和你的 Secrets 配置。

| 模式 | 作用 | 主要用途 |
| --- | --- | --- |
| `daily` | 生成股市全景内参 | 盘前查看过去 24 小时左右的重要新闻、主线、利好利空和情绪判断 |
| `funds` | 生成主力资金雷达 | 基于行业资金流、板块涨跌和近期消息，整理资金主线与次日市场态度 |
| `monitor` | 分钟级双通道监控 | 用规则即时推送重要新闻，并对自选股记录分钟行情、提醒短时大幅异动；推送到监控频道 |
| `periodic` | 盘中茶歇简报 | 对盘中新闻做简洁摘要，帮助快速了解当前市场信息流 |
| `after_market` | 每日收盘复盘 | 基于盘后/下午新闻生成收盘复盘；代码中会在周末跳过发送 |
| `global` | 国际宏观与板块雷达 | 从近 3 小时新闻中提炼可能影响全球市场或 A 股映射的海外事件，推送到监控频道 |
| `recommend` | AI 每日股票观察 | 从热门股票和近期新闻中选择一个观察标的，保存到 `stock_pick.json` 并写入 `history.csv` |
| `track` | 跟踪已选观察标的 | 读取 `stock_pick.json` 中的标的，获取最新行情并生成简短跟踪观点 |
| `review` | 历史表现回看 | 读取 `history.csv`，计算最近记录的表现、胜率和平均收益，并推送结果 |

> 说明：`SUPPORTED_ANALYSIS_MODES` 与 `REQUIRED_ENV_BY_MODE` 在 `main.py` 中分别维护。README 仅说明当前代码支持的分发模式，不代表每个模式都适合高频运行或能覆盖所有投资场景。

## 5. 环境变量 / GitHub Secrets

如果使用 GitHub Actions，建议在仓库的 **Settings → Secrets and variables → Actions** 中配置以下 Secrets。若本地运行，可以用 `export` 临时设置。

| 变量名 | 作用 | 使用场景 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek API Key，用于新闻翻译、摘要和分析 | `daily`、`funds`、`periodic`、`after_market`、`global`、`recommend`、`track` |
| `TG_BOT_TOKEN` | Telegram 主推送 Bot Token | 主频道推送，例如 `daily`、`funds`、`periodic`、`after_market`、`recommend`、`track`、`review` |
| `TG_CHAT_ID` | Telegram 主推送 Chat ID | 主频道推送 |
| `TG_BOT_TOKEN_MONITOR` | Telegram 监控频道 Bot Token | 监控频道推送，例如 `monitor`、`global` |
| `TG_CHAT_ID_MONITOR` | Telegram 监控频道 Chat ID | 监控频道推送 |
| `GLOBAL_NEWS_RSS` | 默认海外 RSS 地址，可覆盖内置海外 RSS 源 | 海外/外部新闻抓取；未配置时使用代码内默认值 |
| `CUSTOM_NEWS_RSS` | 追加自定义 RSS 源，多个地址用英文逗号分隔 | 额外新闻源，例如财经网站、机构 RSS、个人订阅源 |
| `WATCHLIST_CODES` | 要监控的 A 股代码，逗号分隔 | `monitor` 行情通道；例如 `600519,000001`。为空则只监控新闻 |
| `PRICE_ALERT_MINUTE_CHANGE_PCT` | 短时价格异动阈值（百分比） | `monitor` 行情通道；默认 `1.0` |
| `PRICE_ALERT_COOLDOWN_MINUTES` | 同一股票同方向异动的提醒冷却时间 | `monitor` 行情通道；默认 `15` 分钟 |
| `PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES` | 允许与上一笔行情采样比较的最大间隔 | `monitor` 行情通道；默认 `3` 分钟，避免服务中断后误报 |

示例：

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key"
export TG_BOT_TOKEN="your_telegram_bot_token"
export TG_CHAT_ID="your_telegram_chat_id"
export TG_BOT_TOKEN_MONITOR="your_monitor_bot_token"
export TG_CHAT_ID_MONITOR="your_monitor_chat_id"
export CUSTOM_NEWS_RSS="https://example.com/feed.xml,https://another-site.com/rss"
export WATCHLIST_CODES="600519,000001"
export PRICE_ALERT_MINUTE_CHANGE_PCT="1.0"
```

## 6. 本地运行方法

安装依赖：

```bash
pip install -r requirements.txt
```

运行不同模式：

```bash
python main.py daily
python main.py recommend
python main.py monitor
python main.py global
```

更多示例：

```bash
python main.py funds
python main.py periodic
python main.py after_market
python main.py track
python main.py review
```

### 分钟级监控说明

`monitor` 的一次运行会同时执行两条独立链路：

- **新闻链路**：只推送黑天鹅级突发，以及政策、宏观、行业或市场范围的高重要性新消息；不等待 AI 生成结果。
- **行情链路**：仅在 A 股常规交易时段采集 `WATCHLIST_CODES`，将价格写入本地 SQLite；与上一笔不超过 3 分钟的采样相比，变动达到 `PRICE_ALERT_MINUTE_CHANGE_PCT` 才推送。首次运行只建立基线，不会产生行情异动提醒。

监控状态、原始新闻、行情采样和 Telegram 投递状态保存在 `monitor.db`，因此运行服务时必须把该文件放在持久化磁盘中。Telegram 成功后才会把告警标记为已送达；失败记录会保留，事件再次进入提醒窗口时会重试。系统也会防止两轮监控重叠执行。

为获得分钟级体验，应由常驻 VPS 服务每分钟调用一次：

```bash
python main.py monitor
```

GitHub Actions 更适合作为手动回退或日常任务，不应作为此监控的生产调度器。

如果缺少必要环境变量，程序会在启动时提示缺少哪些 Secrets。

## 7. 数据文件与提示词

| 文件 | 说明 |
| --- | --- |
| `prompts.json` | 外部提示词文件。存在且格式正确时，程序会优先读取这里的 Prompt。 |
| `config/settings.py` | 默认配置、数据源地址、环境变量读取、默认 Prompt。 |
| `stock_pick.json` | `recommend` 模式保存的当前观察标的。 |
| `history.csv` | `recommend` 模式追加的历史观察记录，`review` 模式会读取它做表现回看。 |
| `monitor.db` | `monitor` 的 SQLite 状态库，保存原始新闻、分钟行情和告警投递状态；不提交到 Git。 |

## 8. 海外 RSS 与自定义信息源

项目默认会合并东方财富新闻和外部 RSS 新闻。外部 RSS 的处理逻辑包括：

- 按时间过滤近期新闻。
- 使用 DeepSeek 判断是否为中文。
- 对非中文标题和摘要尝试翻译为简体中文。
- 与东方财富新闻合并后按时间倒序排列，并按标题去重。

你可以通过环境变量覆盖或追加 RSS：

```bash
export GLOBAL_NEWS_RSS="https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"
export CUSTOM_NEWS_RSS="https://example.com/feed.xml,https://another-site.com/rss"
python main.py daily
```

## 9. 风险提示与限制

- 本项目不构成任何投资建议，也不保证分析正确。
- AI 输出可能存在事实错误、遗漏、误读、延迟或过度推断。
- 新闻和行情数据来自第三方接口，可能出现不可用、延迟、字段变化或限流。
- `recommend` 模式只是生成一个观察标的，不代表买入建议。
- `track` 和 `review` 只能基于已有记录做简单跟踪，不能证明策略有效。
- 本项目不会自动交易，也不应被用于无人监督的交易决策。
- 使用前请自行核对新闻原文、行情数据和风险承受能力。

## 10. 适合的使用方式

建议把本项目当作一个“信息整理助手”：

- 早上用 `daily` 快速查看市场主线。
- 盘中用 `monitor` 或 `periodic` 辅助过滤信息噪音。
- 收盘后用 `after_market` 做复盘参考。
- 关注资金流时用 `funds` 看行业资金方向。
- 需要观察标的时用 `recommend` 生成候选，再自行研究基本面、技术面和风险。

最终决策仍应由用户自己完成。
