# stock-news-action

## 1. 项目简介

`stock-news-action` 是一个面向个人使用的投资信息流自动化项目。生产任务由 VPS 定时运行，也可以在本地手动运行；核心流程是用 Python 抓取 A 股市场新闻、行业资金流、热门股、海外 RSS 新闻，再调用 DeepSeek 进行摘要、翻译和分析，最后通过 Telegram Bot 推送给用户。

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

1. **VPS 定时器或本地命令触发**：按计划任务或手动执行 `python main.py <mode>`。
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
| `funds` | 生成主力资金雷达 | 直接展示行业资金流与涨跌是否同向，再对匹配新闻做可验证的结构化推演 |
| `monitor` | 五分钟双通道监控 | 用规则即时推送黑天鹅硬风险，并对自选股记录近五分钟行情、提醒短时大幅异动；推送到监控频道 |
| `periodic` | 盘中茶歇简报 | 先展示可核对的盘中新闻事实，再给出待验证的市场主线与观察条件 |
| `us_premarket` | 美股盘前简报 | 美东盘前提炼隔夜新闻、当日催化与开盘后验证点；没有行情数据时不编造期货或盘前价格 |
| `us_periodic` | 美股盘中茶歇 | 美东午间提炼开盘后仍有效的新闻主线、风险变量和下午验证点；不假设实时盘面表现 |
| `after_market` | 每日收盘复盘 | 先展示当日可核对新闻事实，再给出收盘结构与下个交易日验证点；周末跳过发送 |
| `radar` | 实时标的雷达 | 对配置的 A 股和可选美股数据建立自动短时追踪，按确认、失效或到期推送状态；使用与 `track` 相同的主机器人 |
| `global` | 三小时市场总结 | 汇总近 3 小时国内外的重要市场变化，提炼事实、传导路径和后续验证点；无实质变化时不推送 |
| `recommend` | AI 每日股票观察 | 从热门股票和近期新闻中选择一个观察标的，保存到 `stock_pick.json` 并写入 `history.csv` |
| `swing` | A 股中期观察选股 | 收盘后从成交活跃股中筛选趋势、成交与近三日公司相关信息同时成立的唯一候选；观察期约 45 天 |
| `swing_review` | 中期观察胜率回看 | 每周按 T+20、T+40 交易日统计中期观察记录的胜率（正收益占比）和平均收益 |
| `track` | 跟踪已选观察标的 | 读取 `stock_pick.json` 中的标的，获取最新行情并生成简短跟踪观点 |
| `review` | 历史表现回看 | 读取 `history.csv`，按 T+1、T+5、T+20 交易日统一计算正收益占比和平均收益 |
| `daily_health` | VPS 每日健康提醒 | 向监控 Telegram 频道推送最近一次任务状态；失败、异常或状态过期会明确标红 |
| `maintenance` | 状态库维护 | 清理过期新闻、报价、候选和告警，并生成一个本地 SQLite 一致性备份 |
| `telegram_listener` | 雷达交互监听服务 | 常驻接收雷达消息按钮，用于延长或停止已自动开始的追踪 |
| `status_panel` | 监控状态面板 | 向监控 Telegram 频道发送可置顶的“📊 状态”按钮 |
| `yfinance_dev` | Yahoo Finance 两层开发探针 | 第一层查询行情候选，第二层补充近期可追溯事件证据；仅输出本地测试报告，不发送 Telegram、不创建候选、不应部署到 VPS |

> 说明：`SUPPORTED_ANALYSIS_MODES` 与 `REQUIRED_ENV_BY_MODE` 在 `main.py` 中分别维护。README 仅说明当前代码支持的分发模式，不代表每个模式都适合高频运行或能覆盖所有投资场景。

## 5. 环境变量 / GitHub Secrets

如果使用 GitHub Actions，建议在仓库的 **Settings → Secrets and variables → Actions** 中配置以下 Secrets。若本地运行，可以用 `export` 临时设置。

| 变量名 | 作用 | 使用场景 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek API Key，用于新闻翻译、摘要和分析 | `daily`、`funds`、`periodic`、`after_market`、`global`、`recommend`、`track` |
| `TG_BOT_TOKEN` | Telegram 主推送 Bot Token | 主频道推送，例如 `daily`、`funds`、`periodic`、`after_market`、`recommend`、`track`、`review`、`radar` |
| `TG_CHAT_ID` | Telegram 主推送 Chat ID | 主频道推送（也用于 `radar` 按钮交互） |
| `TG_BOT_TOKEN_MONITOR` | Telegram 监控频道 Bot Token | 监控频道推送，例如 `monitor`、`global` |
| `TG_CHAT_ID_MONITOR` | Telegram 监控频道 Chat ID | 监控频道推送 |
| `GLOBAL_NEWS_RSS` | 默认海外 RSS 地址，可覆盖内置海外 RSS 源 | 海外/外部新闻抓取；未配置时使用代码内默认值 |
| `CUSTOM_NEWS_RSS` | 追加自定义 RSS 源，多个地址用英文逗号分隔 | 额外新闻源，例如财经网站、机构 RSS、个人订阅源 |
| `CSRC_NEWS_ENABLED` | 启用中国证监会高信号公告抓取 | 设为 `1` 后启用；仅保留监管、退市、融资融券等高信号条目；日期精度不足时不用于分钟级推送 |
| `SSE_ANNOUNCEMENTS_ENABLED` | 启用上交所高信号公告抓取 | 设为 `1` 后启用；过滤做市等常规公告，仅保留交易、监管、停复牌等条目 |
| `GDELT_DISCOVERY_ENABLED` | 启用 GDELT 全球事件线索层 | 设为 `1` 后启用；只作待核验线索，不会直接触发 Telegram 提醒 |
| `TRUMP_MEDIA_RELAY_ENABLED` | 启用特朗普帖文的权威媒体转述层 | 设为 `1` 后，通过个人 Google News RSS 阅读器发现 Reuters/AP 中明确提到 Truth Social 的报道；保留报道链接，不把单一报道视为交易结论 |
| `TRUMP_MEDIA_RELAY_MAX_RECORDS` | 单次最多读取的权威媒体报道数 | 默认 `10`，避免非必要地扩大查询范围 |
| `TRUTH_SOCIAL_ENABLED` | 启用特朗普 Truth Social 公开帖文抓取 | 设为 `1` 后低频读取公开帖文；无需账号或密钥，若被反爬页面拦截会明确记录失败 |
| `TRUTH_SOCIAL_ACCOUNT_ID` | Truth Social 账户数字 ID | 默认特朗普账户 `107780257626128497`；仅在改为跟踪其他公开账户时调整 |
| `TRUTH_SOCIAL_ACCOUNT_USERNAME` | 公开帖文链接中的账户名 | 默认 `realDonaldTrump` |
| `TRUTH_SOCIAL_MAX_POSTS` | 单次最多读取的公开帖文数 | 默认 `10`，最大 `40`，避免高频抓取 |
| `SEC_WATCHLIST_TICKERS` | SEC 披露观察清单，美股代码逗号分隔 | 例如 `AAPL,NVDA,TSLA`；必须同时配置合规识别信息 |
| `SEC_USER_AGENT` | SEC 请求识别信息 | 必须含真实可联系邮箱，例如 `stock-news-action your-email@example.com`；不配置则 SEC 明确跳过 |
| `SEC_MAX_FILINGS_PER_TICKER` | 每只美股最多读取的近期披露数 | 默认 `3`；只读取 8-K、6-K、10-Q、10-K、20-F、40-F 等披露索引 |
| `MARKET_ALERT_INTERACTION_ENABLED` | 为紧急市场提醒显示事件跟踪按钮 | 设为 `1` 后显示“继续跟踪 2 小时／停止跟踪／查看原文”；群组还必须配置管理员用户 ID |
| `WATCHLIST_CODES` | 要监控的 A 股代码，逗号分隔 | `monitor` 行情通道；例如 `600519,000001`。为空则只监控新闻 |
| `MONITOR_MARKET_ALERT_DEDUP_MINUTES` | 紧急市场提醒的跨来源去重窗口 | `monitor`；默认 `60` 分钟；相同或高度相似事件只投递一次，新数字或更高紧急级别仍会投递 |
| `PRICE_ALERT_MINUTE_CHANGE_PCT` | 短时价格异动阈值（百分比） | `monitor` 行情通道；默认 `1.0` |
| `PRICE_ALERT_COOLDOWN_MINUTES` | 同一股票同方向异动的提醒冷却时间 | `monitor` 行情通道；默认 `15` 分钟 |
| `PRICE_ALERT_MAX_COMPARISON_GAP_MINUTES` | 允许与上一笔行情采样比较的最大间隔 | `monitor` 行情通道；默认 `6` 分钟，匹配五分钟定时器及其抖动，并避免服务中断后误报 |
| `SWING_*` | A 股中期观察筛选条件 | `swing`；默认收盘后按 20/60 日趋势、近 5 日不过热、成交量与近三日公司相关信息筛选，观察期 45 天 |
| `STATE_DIR` | 持久化运行与观察状态目录 | VPS 建议 `/var/lib/stock-news-action`；未设置时仍使用仓库目录，`recommend/track/review` 的状态会一并保存到这里 |
| `STATE_BACKUP_DIR` | SQLite 本地备份目录 | 默认 `STATE_DIR/backups`；每天维护任务生成一个一致性 `.sqlite3` 备份，仍建议将该目录异地复制 |
| `RUN_STATUS_DIR` | 分模式健康状态目录 | 每个模式独立保存心跳，避免某个无关任务覆盖另一个模式的失败状态 |
| `HEALTH_REQUIRED_MODES` | 每日健康提醒必须检查的模式 | 默认 `daily,monitor`；逗号分隔，例如 `daily,monitor,radar` |
| `TELEGRAM_FAILURE_ALERTS_ENABLED` | 每次 systemd 失败时立即推送 Telegram | 默认 `false`；失败仍保留在日志、健康心跳和状态面板中 |
| `CN_MARKET_HOLIDAYS` / `US_MARKET_HOLIDAYS` | 中美交易所例外休市日 | `YYYY-MM-DD` 逗号分隔；周末自动跳过，列出的日期不会被误判为数据失败 |
| `DB_RETENTION_DAYS` | 监控 SQLite 历史保留天数 | 默认 `30`；只清理过期新闻、报价、已结束候选和告警，活跃追踪不受影响 |
| `DB_BACKUP_RETENTION_DAYS` | 本地 SQLite 备份保留天数 | 默认 `14`；只删除维护任务生成的旧备份 |
| `HTTP_GET_MAX_ATTEMPTS` / `HTTP_GET_RETRY_BASE_SECONDS` | 数据 GET 请求的临时失败重试 | 默认 `2` 次、`0.5` 秒基础退避；只重试网络错误、429 和 5xx，不会重试 Telegram 发送以避免重复消息 |
| `RADAR_A_SHARE_CODES` | 雷达专用 A 股代码，逗号分隔 | `radar`；与原有 `WATCHLIST_CODES` 分离，避免改变现有监控行为 |
| `RADAR_A_SHARE_MINUTE_CHANGE_PCT` | A 股短时异动阈值（百分比） | `radar`；默认 `1.5` |
| `RADAR_A_SHARE_HOT_POOL_ENABLED` | 启用 A 股热门低价线索池 | `radar`；仅从成交额热门池筛选低价、当日涨幅 `2–8%` 的早期走强标的，不是全 A 股扫描 |
| `RADAR_MAX_CANDIDATES_PER_SYMBOL_PER_SESSION` | 同一标的单个交易日的首次推送上限 | `radar`；默认 `1`，只计初始 Telegram 消息已成功送达的候选 |
| `RADAR_SYMBOL_MUTE_DAYS` | 点击“不再推送”后的静默天数 | `radar`；默认 `7`，会停止当前追踪并抑制后续同标的候选 |
| `POLYGON_API_KEY` | Polygon 美股行情 Key | `radar` 的美股扫描与单标的追踪；不配置则跳过美股，不伪装为正常取数 |
| `YFINANCE_EXPERIMENTAL_RADAR_ENABLED` | 启用 Yahoo 美股实验性线索池 | `radar`；最多读取 Yahoo 候选页的 250 条返回结果，默认每 10 分钟一次、每轮最多推送 1 条，不保证完整或实时 |
| `YFINANCE_DEV_TICKERS` | Yahoo Finance 开发测试代码，逗号分隔 | 仅 `yfinance_dev`；最多 20 只，不属于生产雷达配置 |
| `YFINANCE_DEV_BROAD_SCAN` | Yahoo 广泛市场测试开关 | 仅 `yfinance_dev`；设为 `1` 时运行最多 250 条候选的筛选页面，不可与 `YFINANCE_DEV_TICKERS` 同时设置 |
| `YFINANCE_DEV_EVENT_MAX_CANDIDATES` | 事件层最多核验的候选数 | 仅 `yfinance_dev`；默认 `20`，避免对候选页面逐只高频请求 |
| `YFINANCE_DEV_EVENT_ITEMS_PER_SYMBOL` | 每只候选最多读取的 Yahoo 新闻数 | 仅 `yfinance_dev`；默认 `3` |
| `YFINANCE_DEV_EVENT_MAX_AGE_HOURS` | 事件层新闻的最大时效 | 仅 `yfinance_dev`；默认 `24` 小时，过期或无时间新闻不作近期证据 |
| `US_RADAR_MIN_PRICE` / `US_RADAR_MAX_PRICE` | 美股候选价格区间 | 默认 `$1–5` |
| `US_RADAR_MIN_DAY_CHANGE_PCT` / `US_RADAR_MAX_DAY_CHANGE_PCT` | 美股低价股的早期涨幅区间 | 默认 `3–15`；在成交额达标时更早入池，同时过滤已大幅拉升的后段行情 |
| `US_RADAR_MIN_DOLLAR_VOLUME` | 美股最小成交额（美元） | 默认 `1000000`，过滤低流动性噪音 |
| `TG_INTERACTION_ALLOWED_USER_IDS` | 可操作雷达按钮的 Telegram 用户 ID | 群聊必须配置；私聊默认仅允许该私聊账号 |

示例：

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key"
export TG_BOT_TOKEN="your_telegram_bot_token"
export TG_CHAT_ID="your_telegram_chat_id"
export TG_BOT_TOKEN_MONITOR="your_monitor_bot_token"
export TG_CHAT_ID_MONITOR="your_monitor_chat_id"
export CUSTOM_NEWS_RSS="https://example.com/feed.xml,https://another-site.com/rss"
export CSRC_NEWS_ENABLED="1"
export SSE_ANNOUNCEMENTS_ENABLED="1"
export GDELT_DISCOVERY_ENABLED="1"
export TRUMP_MEDIA_RELAY_ENABLED="1"
export TRUTH_SOCIAL_ENABLED="1"
export SEC_WATCHLIST_TICKERS="AAPL,NVDA,TSLA"
export SEC_USER_AGENT="stock-news-action your-email@example.com"
export MARKET_ALERT_INTERACTION_ENABLED="1"
export WATCHLIST_CODES="600519,000001"
export PRICE_ALERT_MINUTE_CHANGE_PCT="1.0"
export RADAR_A_SHARE_CODES="600519,000001"
export POLYGON_API_KEY="your_polygon_key"
export TG_INTERACTION_ALLOWED_USER_IDS="your_telegram_user_id"
```

## 6. 本地运行方法

首次创建独立开发环境并安装依赖：

```bash
make setup
```

日常验证统一使用项目入口，避免误用系统 Python：

```bash
make test      # 全部测试
make lint      # 静态检查
make validate  # 部署清单检查
make check     # 依次执行以上三项
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
python main.py us_premarket
python main.py us_periodic
python main.py after_market
python main.py track
python main.py review
python main.py radar
python main.py swing
python main.py swing_review
python main.py maintenance
python main.py metrics
```

`python main.py metrics monitor` 会额外显示本轮和累计的新闻筛选漏斗、已送达提醒、行情样本及异常数据源。Telegram 中“继续跟踪、停止、静默”的有效操作会以不含用户身份和消息内容的汇总计数显示，用于判断提醒是否需要收紧或优化。

### 五分钟监控说明

`monitor` 的一次运行会同时执行两条独立链路：

- **新闻链路**：只推送黑天鹅级突发和待核实硬风险；政策、宏观、行业或市场范围的重要变化进入每三小时一次的市场总结，不在五分钟监控中重复推送。
- **行情链路**：仅在 A 股常规交易时段采集 `WATCHLIST_CODES`，将价格写入本地 SQLite；与上一笔不超过 6 分钟的采样相比，变动达到 `PRICE_ALERT_MINUTE_CHANGE_PCT` 才推送。首次运行只建立基线，不会产生行情异动提醒。

监控状态、原始新闻、行情采样和 Telegram 投递状态保存在 `monitor.db`，因此运行服务时必须把该文件放在持久化磁盘中。Telegram 成功后才会把告警标记为已送达；失败记录会保留，事件再次进入提醒窗口时会重试。系统也会防止两轮监控重叠执行。`maintenance` 每天会清理超过保留期的历史并生成 SQLite 一致性备份和恢复压缩包；本地备份不能替代异地备份。

### 异地恢复备份

默认不会上传任何数据。若服务器已配置 [`rclone`](https://rclone.org/) 的加密远端，将环境文件中的 `OFFSITE_BACKUP_ENABLED=true` 和 `OFFSITE_BACKUP_RCLONE_TARGET=remote:stock-news-action` 一并设置。每次 `maintenance` 会上传一个包含一致性 `monitor.db`、历史、观察标的、运行状态和指标的 `stock-news-state-*.zip`。上传失败、超时、远端未配置或未安装 rclone 都会让维护任务失败并触发既有故障通知，绝不将其报告为异地备份成功。

恢复前先停止相关服务并下载所需压缩包；先用 `unzip -t` 校验，再将其中的 `monitor.db`、`history.csv`、`stock_pick.json`、运行状态文件恢复到 `STATE_DIR`。恢复后重新启动服务并执行 `python main.py health monitor`。建议先在非生产目录演练恢复，且不要把环境文件或 Telegram/AI 密钥放入任何备份。

“紧急市场提醒”会在投递前检查最近 60 分钟已成功发送的内容：同链接、同标题，或标题和摘要都高度相似时只发一次，即使来自不同媒体。原始新闻仍会完整保存；数字、比例、金额等事实变化仍会作为新进展投递。

黑天鹅筛选完全使用低延迟规则：只有命中具体风险事件（如实际军事打击、金融或支付系统风险、重大自然灾害、制裁升级），并结合来源可信度和重要性评分后，才会标为紧急。历史回顾、演习、影视或假设情景会被过滤；来源不足或标注为未经证实的事件只会标为“待核实风险提示”，不会伪装成已确认的紧急事实。

紧急市场提醒只保留事件、关键事实、市场含义、关注变量和原文链接；分类、重要性、影响范围等重复字段不会单独展示。市场含义只说明当前最关键的传导问题，关注变量用于指出下一步应核对什么。所有内容均为条件化观察，不构成买卖建议。

### Telegram 消息样式

日常摘要、资金雷达、盘中/盘后简报、海外简报、健康提醒和追踪更新都使用同一阅读顺序：标题与时间、必要的来源、重点、怎么看或下一步，最后才放原文。分类、重要性、影响范围等内部标签不再逐项展示；旧版 AI 输出里的方括号标题也会自动转成普通短标题。

生产定时器每五分钟调用一次，比较窗口已与该周期对齐：

```bash
python main.py monitor
```

GitHub Actions 更适合作为手动回退或日常任务，不应作为此监控的生产调度器。

`global` 应只在 VPS 上每三小时运行一次。可使用
`deploy/systemd/stock-news-global.timer`；若服务器使用
`stock-news-action@.service` 模板，将其 `Unit=` 改为
`stock-news-action@global.service` 后启用。GitHub Actions 不保留该任务，避免与 VPS 重复推送。

如果缺少必要环境变量，程序会在启动时提示缺少哪些 Secrets。

### 互动式实时标的雷达

`radar` 是独立于原 `monitor` 的候选追踪链路，不会自动交易，也不会修改
`WATCHLIST_CODES`。它与 `track` 使用同一个主机器人和聊天；流程是：

1. 对 `RADAR_A_SHARE_CODES` 每分钟建立 A 股报价基线；只有短时变动达到
   `RADAR_A_SHARE_MINUTE_CHANGE_PCT` 才建立候选。
2. 如配置了 `POLYGON_API_KEY`，在美东盘前、盘中和盘后扫描满足价格、早期涨幅区间和成交额
   过滤条件的美股候选；没有可核对新闻时会明确标为“未确认催化”。
3. 候选一出现先在后台静默追踪，不等待 Telegram 点击。只有经过默认 3 分钟确认窗口、
   行情仍可核对且反转未超过 `0.5%` 时，才发送一条“已确认”消息；确认期内失效和自动
   到期都不会单独推送。已确认后若明显失效，才补充一条风险提醒。
4. 同一标的单个交易日默认只发送一次初始候选；初始消息送达失败不会被误记为已推送。
   Telegram 可延长或停止本次追踪，也可选择在配置天数内不再推送该标的。它不是下单入口，
   也不会让旧价格直接变成操作指令；只有允许的 Telegram 用户可以点击生效。

### A 股中期观察选股

`swing` 用于替代高频异动筛选的低噪音使用方式。它在每个 A 股交易日收盘后运行一次，先从成交额靠前的股票中读取近期日线，只有同时满足以下条件才会发送一只观察标的：

1. 收盘价高于 20 日均线、20 日均线高于 60 日均线，且 20/60 日趋势达到配置门槛；
2. 近 5 日没有超过配置的过热涨幅，距离近 60 日高点未过远，且近期成交量没有萎缩；
3. 近三日存在明确提到该公司的可核对新闻或公告线索。

没有合格标的时不会为了凑数推送；已有中期观察标的时，会在默认 45 天观察期内停止新增。每个中期标的会写入 `history.csv` 并标记为 `medium_term`；`swing_review` 每周在 T+20 与 T+40 交易日口径分别统计胜率（正收益占比）和平均收益。统计只用于检查历史表现，样本少或行情不足时不会被写成策略有效或未来收益保证。

美股首版通过 Polygon 快照接口按分钟取数，因此实际新鲜度受你的 Polygon 套餐和交易所
数据权限影响。若数据源返回失败，日志会明确报错；未配置 Key 时则只运行 A 股部分。
不要把延迟行情或单次异动理解为交易信号。

雷达与按钮监听需要分别启用：

```bash
python main.py radar
python main.py telegram_listener
```

`telegram_listener` 使用 Telegram 长轮询，不需要向公网开放 Webhook 端口。它会监听主机器人
的雷达按钮和监控机器人的状态按钮；启用紧急市场事件跟踪时，也会监听监控机器人的事件按钮。每个 Bot 只能有一个
监听进程；如果该 Bot 已设置 Webhook，需要先清理 Webhook 或改用该 Webhook 接收按钮回调。

### Yahoo Finance 开发测试

`yfinance_dev` 是单独的本地开发探针，分为两层：第一层核对 Yahoo Finance / yfinance
返回的价格、前收、成交量能否映射到当前美股筛选阈值；第二层只为第一层候选读取少量、
带时间和链接的近期 Yahoo 新闻。第二层的 `recent_traceable_event_found` 仅表示“找到近期
可追溯来源”，不是“新闻已证明导致上涨”；`no_recent_traceable_event` 与
`event_fetch_failed` 必须保留为待确认或失败，不能写成利好结论。

它不会读取或发送 Telegram，不会写入 `monitor.db`，也不能加入 systemd 定时器。yfinance
并非 Yahoo 官方背书的数据接口，其数据仅用于研究和开发验证，不得标记为“全市场实时确认”。

先安装开发依赖，再只为本次终端会话指定少量代码：

```bash
pip install -r requirements-dev.txt
export YFINANCE_DEV_TICKERS="SOFI,LCID,RKLB"
python main.py yfinance_dev
```

输出中的 `matches_current_us_radar_filters` 只代表字段按现有阈值的机械匹配；它不会产生
推送，也不构成交易、买卖或全市场扫描结论。

若要测试 Yahoo 的广泛市场筛选，请不要设置 `YFINANCE_DEV_TICKERS`，改为：

```bash
unset YFINANCE_DEV_TICKERS
export YFINANCE_DEV_BROAD_SCAN=1
python main.py yfinance_dev
```

此测试由 Yahoo 的筛选器先过滤美国股票，再最多返回 250 条结果；第二层再按顺序最多核验
20 个候选的近期新闻。它只能用于验证“广泛候选池 + 事件证据”的字段与规则，不能证明未
返回的股票没有异动，更不能替代正式全市场实时行情。

若只要求低成本的“有用线索”而不是完整实时覆盖，可在 VPS 环境文件中显式开启
`RADAR_A_SHARE_HOT_POOL_ENABLED=1` 与 `YFINANCE_EXPERIMENTAL_RADAR_ENABLED=1`。
前者只从 A 股成交额热门池中筛选低价强势股；后者只读取 Yahoo 返回的候选页，并在消息中
标注实验性数据限制。两者都不是完整市场扫描，不应用于自动交易。

## 7. 数据文件与提示词

| 文件 | 说明 |
| --- | --- |
| `prompts.json` | 外部提示词文件。存在且格式正确时，程序会优先读取这里的 Prompt。 |
| `config/settings.py` | 默认配置、数据源地址、环境变量读取、默认 Prompt。 |
| `stock_pick.json` | `recommend` 模式保存的当前观察标的。 |
| `history.csv` | `recommend` 模式追加的历史观察记录，`review` 模式会读取它做表现回看。 |
| `monitor.db` | `monitor` 与 `radar` 的 SQLite 状态库，保存原始新闻、行情基线、候选追踪、按钮游标和告警投递状态；不提交到 Git。 |

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

## 9. 第二批专用信息源

除 RSS 外，项目还支持三类专用来源：

- 中国证监会与上交所：只接收明确的监管、交易制度、停复牌、退市等高信号公告。官网列表通常只有日期，没有精确发布时间，因此它们会进入日报/盘中分析，但不会被伪装成刚发生的高频提醒。
- SEC EDGAR：只跟踪你明确配置的美股代码。SEC 要求自动访问在请求头中声明可联系身份；未配置 `SEC_USER_AGENT` 或观察清单时，系统记录“跳过”而不会假装获取成功。
- GDELT：用于全球事件发现。GDELT 返回的标题只会带“待核验线索”标记进入内部新闻流，不会直接推送到 Telegram；需要以原始报道或官方公告确认后，才应作为事实使用。
- 特朗普帖文媒体转述：个人 Google News RSS 阅读器仅保留 Reuters/AP 中明确出现“Donald Trump”和“Truth Social”的报道。报道原文而非 RSS 聚合结果才是可核验依据；它会进入新闻流，但不会把单条报道自动写成交易结论。
- Truth Social（特朗普）：可选读取特朗普账户的公开帖文，不需登录或密钥，默认关闭。帖文作为一手公开信息保留原文链接；未按内容自动认定为市场重大事件。若接口返回反爬拦截页或异常格式，数据源健康状态会标记为失败，而不会伪装成“没有新帖”。

### 事件跟踪按钮

为紧急市场提醒启用 `MARKET_ALERT_INTERACTION_ENABLED=1` 后，消息会附带“继续跟踪 2 小时”“停止跟踪”和“查看原文”。点击后，系统仍按一分钟周期检查已接入新闻源；仅在出现至少两个标题关键词重合的新来源时推送后续，并在两小时结束时给出一次结束提示。它不会自动搜索全网，也不会产生买卖指令。

若提醒发在群组，必须把你自己的 Telegram 数字用户 ID 写入 `TG_INTERACTION_ALLOWED_USER_IDS`，例如 `TG_INTERACTION_ALLOWED_USER_IDS=123456789`；否则群成员不应能改变跟踪状态。私聊中则自动仅允许该私聊用户操作。需要查询 ID 时，打开该机器人私聊并发送 `/id`，机器人会只在私聊中回复你的数字 ID。

## 10. 风险提示与限制

- 本项目不构成任何投资建议，也不保证分析正确。
- AI 输出可能存在事实错误、遗漏、误读、延迟或过度推断。
- 新闻和行情数据来自第三方接口，可能出现不可用、延迟、字段变化或限流。
- `recommend` 模式只是生成一个观察标的，不代表买入建议。
- `track` 和 `review` 只能基于已有记录做简单跟踪，不能证明策略有效。
- 本项目不会自动交易，也不应被用于无人监督的交易决策。
- `radar` 的“确认／失效”只是预设信息条件是否仍成立，不是个性化买卖、仓位或止损建议。
- 使用前请自行核对新闻原文、行情数据和风险承受能力。

## 10. 适合的使用方式

建议把本项目当作一个“信息整理助手”：

- 早上用 `daily` 快速查看市场主线。
- 盘中用 `monitor` 或 `periodic` 辅助过滤信息噪音。
- 收盘后用 `after_market` 做复盘参考。
- 关注资金流时用 `funds` 看行业资金方向。
- 需要观察标的时用 `recommend` 生成候选，再自行研究基本面、技术面和风险。

最终决策仍应由用户自己完成。

## 11. VPS 生产调度与健康检查

GitHub Actions 只保留代码测试；所有 Telegram 生产推送都由 VPS 承担。仓库提供
`deploy/systemd/` 示例，避免生产调度只存在于服务器的手工配置中。

建议部署目录和专用用户：

```bash
sudo useradd --system --home /opt/stock-news-action --shell /usr/sbin/nologin stockbot
sudo cp deploy/systemd/* /etc/systemd/system/
sudo cp deploy/stock-news-action.env.example /etc/stock-news-action.env
sudo chmod 600 /etc/stock-news-action.env
```

编辑 `/etc/stock-news-action.env`，填入真实 Secrets。不要把修改后的文件提交到仓库。
然后启用需要的定时器：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-news-monitor.timer
sudo systemctl enable --now stock-news-daily.timer
sudo systemctl enable --now stock-news-periodic.timer
sudo systemctl enable --now stock-news-us-premarket.timer
sudo systemctl enable --now stock-news-us-periodic.timer
sudo systemctl enable --now stock-news-after-market.timer
sudo systemctl enable --now stock-news-funds.timer
sudo systemctl enable --now stock-news-daily-health.timer
sudo systemctl enable --now stock-news-radar.timer
sudo systemctl enable --now stock-news-swing.timer
sudo systemctl enable --now stock-news-swing-review.timer
sudo systemctl enable --now stock-news-maintenance.timer
sudo systemctl enable --now stock-news-interaction.service
systemctl list-timers 'stock-news-*'
```

每次任务结束都会原子写入兼容的 `RUN_STATUS_FILE`，并在 `RUN_STATUS_DIR` 写入该模式独立的状态文件。以下命令会输出最近一次运行摘要；也可指定模式。失败、状态文件损坏或超过 `HEALTH_MAX_AGE_MINUTES` 时命令返回非零状态：

```bash
python main.py health
python main.py health monitor
python main.py metrics
python main.py metrics monitor
```

`metrics` 会汇总每种任务的成功、部分完成、失败、投递失败次数及最近异常数据源；它只保存运行状态与计数，不保存密钥或消息正文。指标写入 `METRICS_FILE`，与心跳文件一起放在持久化目录中。

`stock-news-daily-health.timer` 会在每天 08:40（上海时间）向监控 Telegram
频道发送一条健康提醒。它会检查 `HEALTH_REQUIRED_MODES` 中每个模式独立的任务、数据抓取、上轮 Telegram 投递和状态
文件年龄；只要发现失败、非成功状态或状态超过 `HEALTH_MAX_AGE_MINUTES`，消息会
标为异常，并让该次 systemd 任务失败，便于在日志中追踪。

如果服务器已经使用了不同前缀的模板服务（例如
`stock-news-action@.service`），则每日健康定时器也必须使用相同前缀：将其
`Unit=` 改为 `stock-news-action@daily_health.service`，并以
`stock-news-action-daily-health.timer` 的名称启用。

`stock-news-funds.timer` 会在每个工作日上海时间 15:10 运行主力资金雷达。若服务器
使用 `stock-news-action@.service` 模板，定时器也必须使用相同前缀：将 `Unit=` 改为
`stock-news-action@funds.service`，并以 `stock-news-action-funds.timer` 的名称启用。
资金雷达会使用现有的 `TG_BOT_TOKEN` 和 `TG_CHAT_ID`。

所有 `stock-news@*.service` 及交互监听服务失败时，systemd 会触发
`stock-news-failure@.service`。为避免部分完成或短暂服务失败反复打扰，默认不会立即发送
Telegram；故障仍会保留在 systemd journal、独立健康心跳和每日健康提醒中。若确实需要每次
立即通知，可在服务器环境文件中设置 `TELEGRAM_FAILURE_ALERTS_ENABLED=true`。生产环境仍建议把
`STATE_BACKUP_DIR` 异地同步，并接入独立于 Telegram 的主机监控。

### 监控状态按钮

保持 `stock-news-interaction.service` 运行后，执行一次以下命令，会在监控 Telegram 频道
发送带“📊 状态”按钮的消息；将该消息置顶即可作为一键入口。每条程序推送也会附带同一个紧凑按钮，
而雷达、事件追踪等原有按钮会保留：

```bash
sudo -u stockbot /opt/stock-news-action/.venv/bin/python /opt/stock-news-action/main.py status_panel
```

点击按钮会读取 `HEALTH_REQUIRED_MODES` 中每个模式最近一次运行的独立心跳，显示正常、部分
完成、失败或状态过期。群聊中只有 `TG_INTERACTION_ALLOWED_USER_IDS` 允许的账号能刷新。

市场监控的“紧急市场提醒”使用紧凑格式：先给事件与关键事实，再只指出一个最关键的
市场含义和一个应核对的变量。三小时市场总结按政策、宏观、资金、行业、公司或海外类别
提炼；紧急提醒按冲突、金融风险、供应链、清算或灾害等风险类型提炼。该内容仅用于观察
与核对，不构成买卖结论。

监控去重文件应放在 `/var/lib/stock-news-action/` 等持久目录。GitHub Actions 的临时
文件系统不会跨运行保存 `monitor_seen.json`，因此不应把 Actions 当作有状态监控器。

## 12. AI 调用与复盘口径

- 中文检测在本地完成，只有非中文 RSS 才调用翻译模型。
- 语义去重先用标题相似度筛选，只有疑似重复的小集合交给 DeepSeek。
- `review` 不再把不同持有天数的当前收益混为一个“胜率”，而是分别统计
  T+1、T+5、T+20 交易日表现。
- 尚未走完相应交易周期的记录不会混入该周期统计。
