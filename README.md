# stock-news-action

## 添加你的信息源（含海外来源）

现在默认会拉取一个海外 RSS 信息源（Reuters），并支持通过环境变量追加你的 RSS。

```bash
export GLOBAL_NEWS_RSS="https://feeds.reuters.com/reuters/worldNews"
export CUSTOM_NEWS_RSS="https://example.com/feed.xml,https://another.com/rss"
python main.py daily
```

说明：
- 默认抓取东方财富 + `GLOBAL_NEWS_RSS`（海外）。
- `CUSTOM_NEWS_RSS` 可继续追加你自己的多个来源。
- 海外/外部新闻会先用 DeepSeek 判断是否中文；若不是中文会自动翻译为简体中文。
- 翻译后的新闻与东方财富新闻合并后，会按时间倒序、按标题去重，输出更精简的新闻流。
