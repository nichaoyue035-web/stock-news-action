"""Official Chinese, SEC, and GDELT source adapters."""

from __future__ import annotations

import datetime
import re
import time
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlparse

import requests

from config import settings
from core.http_client import request_get
from core.news_processing import _has_keyword
from core.news_source_common import _parse_datetime, _strip_html
from core.source_health import record_data_source_health, redact_error_detail
from utils.notifier import log_error, log_info


def _redact_sensitive_text(value: Any) -> str:
    return redact_error_detail(value)


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CSRC_NEWS_URL = "https://www.csrc.gov.cn/"
SSE_ANNOUNCEMENTS_URL = (
    "https://www.sse.com.cn/disclosure/announcement/general/index.shtml"
)
GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

CSRC_MATERIAL_TERMS: tuple[str, ...] = (
    "暂停交易",
    "暂停上市",
    "终止上市",
    "退市",
    "风险警示",
    "立案",
    "行政处罚",
    "市场禁入",
    "内幕交易",
    "操纵市场",
    "重大资产重组",
    "融资融券",
    "程序化交易",
    "量化交易",
    "再融资",
    "发行注册",
    "上市公司监管",
)
SSE_MATERIAL_TERMS: tuple[str, ...] = (
    "暂停交易",
    "临时停市",
    "终止上市",
    "退市",
    "风险警示",
    "纪律处分",
    "公开谴责",
    "重大资产重组",
    "系统故障",
    "交易异常",
    "融资融券",
    "指数调整",
    "市场波动",
    "交易规则",
    "停牌",
    "复牌",
)


class _DatedListParser(HTMLParser):
    """Extract dated links from simple official announcement lists."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict[str, str]] = []
        self._li_depth = 0
        self._li_text: list[str] = []
        self._links: list[tuple[str, str]] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        clean_tag = tag.lower()
        if clean_tag == "li":
            if self._li_depth == 0:
                self._li_text = []
                self._links = []
            self._li_depth += 1
            return
        if clean_tag == "a" and self._li_depth:
            self._anchor_href = dict(attrs).get("href") or ""
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._li_depth:
            self._li_text.append(data)
        if self._anchor_href:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        clean_tag = tag.lower()
        if clean_tag == "a" and self._anchor_href:
            title = " ".join(self._anchor_text).strip()
            if title:
                self._links.append((title, self._anchor_href))
            self._anchor_href = ""
            self._anchor_text = []
            return
        if clean_tag != "li" or not self._li_depth:
            return

        self._li_depth -= 1
        if self._li_depth:
            return

        raw_date = " ".join(self._li_text)
        for title, href in self._links:
            self.entries.append({"title": title, "href": href, "raw_date": raw_date})


def _parse_cn_list_datetime(
    raw_value: Any, now: datetime.datetime
) -> Optional[datetime.datetime]:
    """Parse list-page dates without pretending that a date-only item is current."""
    text = str(raw_value or "")
    match = re.search(r"(?<!\d)(20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2})(?!\d)", text)
    if not match:
        return None

    value = match.group(1)
    try:
        if value.count("-") == 2:
            parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
        else:
            parsed = datetime.datetime.strptime(f"{now.year}-{value}", "%Y-%m-%d")
            if parsed.date() > now.date() + timedelta(days=1):
                parsed = parsed.replace(year=now.year - 1)
    except ValueError:
        return None
    return parsed.replace(tzinfo=settings.SHA_TZ)


def _official_request_headers() -> dict[str, str]:
    """Use a transparent, non-browser header for public official pages."""
    return {
        "User-Agent": "stock-news-action/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _fetch_cn_official_news(
    *,
    source_name: str,
    feed_url: str,
    material_terms: tuple[str, ...],
    minutes_lookback: Optional[int],
) -> list[dict[str, Any]]:
    """Fetch only material dated entries from an official Chinese list page."""
    now = datetime.datetime.now(settings.SHA_TZ)
    delta = timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
    threshold = now - delta
    try:
        response = request_get(
            feed_url, headers=_official_request_headers(), timeout=15
        )
        response.raise_for_status()
        parser = _DatedListParser()
        parser.feed(response.text)
        parser.close()
    except (requests.RequestException, ValueError) as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health(source_name, "failed", reason, 0)
        log_error(f"⚠️ {source_name}抓取失败: {reason}")
        return []

    items: list[dict[str, Any]] = []
    for entry in parser.entries:
        title = _strip_html(entry.get("title"))
        published_at = _parse_cn_list_datetime(entry.get("raw_date"), now)
        if not title or published_at is None or published_at < threshold:
            continue
        if not _has_keyword(title, material_terms):
            continue

        items.append(
            {
                "title": title,
                "digest": "官方公告列表仅提供日期；请打开原文核对发布时间和完整条款。",
                "link": urljoin(feed_url, str(entry.get("href") or "")),
                "time_str": published_at.strftime("%H:%M"),
                "datetime": published_at,
                "source": source_name,
                "category": "policy",
                "importance": "high",
                "market_scope": "A股",
                "published_time_precision": "date",
            }
        )
        if len(items) >= settings.CN_OFFICIAL_MAX_ITEMS:
            break

    record_data_source_health(source_name, "success", "", len(items))
    log_info(f"{source_name}抓取成功: matched_count={len(items)}")
    return items


def _sec_request_headers() -> dict[str, str]:
    """Return the SEC-required declared automated-client headers."""
    return {
        "User-Agent": settings.SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    }


def _sec_filing_link(cik: int, accession: str, primary_document: str) -> str:
    """Build a canonical SEC archive link from submission metadata only."""
    clean_accession = str(accession or "").replace("-", "")
    clean_document = quote(str(primary_document or "").lstrip("/"), safe="/._-")
    if clean_accession and clean_document:
        return (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{clean_accession}/{clean_document}"
        )
    return f"https://www.sec.gov/edgar/browse/?CIK={int(cik)}"


def _fetch_sec_edgar_filings(
    minutes_lookback: Optional[int],
) -> list[dict[str, Any]]:
    """Fetch recent filings for an explicit SEC ticker watchlist only."""
    source_name = "SEC EDGAR"
    if not settings.SEC_WATCHLIST_TICKERS:
        record_data_source_health(
            source_name, "skipped", "未配置 SEC_WATCHLIST_TICKERS", 0
        )
        return []
    if not settings.SEC_USER_AGENT:
        record_data_source_health(source_name, "skipped", "未配置 SEC_USER_AGENT", 0)
        return []

    now = datetime.datetime.now(settings.SHA_TZ)
    threshold = now - timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
    try:
        tickers_response = request_get(
            SEC_TICKERS_URL, headers=_sec_request_headers(), timeout=15
        )
        tickers_response.raise_for_status()
        raw_tickers = tickers_response.json()
    except (requests.RequestException, ValueError) as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health(source_name, "failed", reason, 0)
        log_error(f"⚠️ SEC EDGAR 抓取失败: {reason}")
        return []

    ticker_map: dict[str, tuple[int, str]] = {}
    if isinstance(raw_tickers, dict):
        for row in raw_tickers.values():
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            try:
                cik = int(row.get("cik_str"))
            except (TypeError, ValueError):
                continue
            if ticker:
                ticker_map[ticker] = (cik, str(row.get("title") or ticker))

    items: list[dict[str, Any]] = []
    failures: list[str] = []
    requested = 0
    for ticker in settings.SEC_WATCHLIST_TICKERS:
        mapped = ticker_map.get(ticker)
        if mapped is None:
            failures.append(f"未找到代码 {ticker}")
            continue
        if requested:
            time.sleep(0.12)
        requested += 1
        cik, company_name = mapped
        try:
            response = request_get(
                SEC_SUBMISSIONS_URL.format(cik=f"{cik:010d}"),
                headers=_sec_request_headers(),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            failures.append(_redact_sensitive_text(exc))
            continue

        recent = (
            payload.get("filings", {}).get("recent", {})
            if isinstance(payload, dict)
            else {}
        )
        forms = recent.get("form", []) if isinstance(recent, dict) else []
        if not isinstance(forms, list):
            failures.append(f"{ticker} 返回格式异常")
            continue

        selected = 0
        for index, raw_form in enumerate(forms):
            form = str(raw_form or "").upper().strip()
            if form not in settings.SEC_EDGAR_ALLOWED_FORMS:
                continue
            acceptance_times = recent.get("acceptanceDateTime", [])
            accepted_at = _parse_datetime(
                acceptance_times[index]
                if isinstance(acceptance_times, list) and index < len(acceptance_times)
                else ""
            )
            if accepted_at is None or accepted_at < threshold:
                continue

            accessions = recent.get("accessionNumber", [])
            documents = recent.get("primaryDocument", [])
            report_dates = recent.get("reportDate", [])
            accession = (
                str(accessions[index])
                if isinstance(accessions, list) and index < len(accessions)
                else ""
            )
            primary_document = (
                str(documents[index])
                if isinstance(documents, list) and index < len(documents)
                else ""
            )
            report_date = (
                str(report_dates[index])
                if isinstance(report_dates, list) and index < len(report_dates)
                else ""
            )
            items.append(
                {
                    "title": f"SEC 披露｜{ticker}｜{form}",
                    "digest": (
                        f"{company_name} 已向 SEC 提交 {form}"
                        f"{f'（报告期 {report_date}）' if report_date else ''}；"
                        "仅为披露索引，请打开原文确认具体事项。"
                    ),
                    "link": _sec_filing_link(cik, accession, primary_document),
                    "time_str": accepted_at.strftime("%H:%M"),
                    "datetime": accepted_at,
                    "source": source_name,
                    "category": "company",
                    "importance": "medium",
                    "market_scope": "美股",
                }
            )
            selected += 1
            if selected >= settings.SEC_MAX_FILINGS_PER_TICKER:
                break

    if failures and not requested:
        record_data_source_health(source_name, "failed", failures[0], len(items))
    elif failures:
        record_data_source_health(source_name, "partial", failures[0], len(items))
    else:
        record_data_source_health(source_name, "success", "", len(items))
    log_info(
        f"SEC EDGAR 抓取完成: requested_tickers={requested}, returned_count={len(items)}"
    )
    return items


def _fetch_gdelt_discovery_news(
    minutes_lookback: Optional[int],
) -> list[dict[str, Any]]:
    """Fetch GDELT leads without treating the aggregation layer as confirmation."""
    source_name = "GDELT 线索"
    if not settings.GDELT_DISCOVERY_ENABLED:
        record_data_source_health(source_name, "skipped", "未启用", 0)
        return []
    if not settings.GDELT_DISCOVERY_QUERY:
        record_data_source_health(source_name, "skipped", "查询条件为空", 0)
        return []

    now = datetime.datetime.now(settings.SHA_TZ)
    threshold = now - timedelta(minutes=minutes_lookback if minutes_lookback else 1440)
    try:
        response = request_get(
            GDELT_DOC_API_URL,
            params={
                "query": settings.GDELT_DISCOVERY_QUERY,
                "mode": "artlist",
                "format": "json",
                "maxrecords": settings.GDELT_DISCOVERY_MAX_RECORDS,
                "timespan": "1h",
            },
            headers=_official_request_headers(),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        reason = _redact_sensitive_text(exc)
        record_data_source_health(source_name, "failed", reason, 0)
        log_error(f"⚠️ GDELT 线索抓取失败: {reason}")
        return []

    raw_articles = payload.get("articles", []) if isinstance(payload, dict) else []
    if not isinstance(raw_articles, list):
        record_data_source_health(source_name, "failed", "返回格式异常", 0)
        return []

    items: list[dict[str, Any]] = []
    for article in raw_articles:
        if not isinstance(article, dict):
            continue
        title = _strip_html(article.get("title"))
        link = str(article.get("url") or "").strip()
        published_at = _parse_datetime(article.get("seendate"))
        parsed_link = urlparse(link)
        if (
            not title
            or not published_at
            or published_at < threshold
            or parsed_link.scheme not in {"http", "https"}
            or not parsed_link.netloc
        ):
            continue
        domain = str(article.get("domain") or parsed_link.netloc).strip()
        items.append(
            {
                "title": f"GDELT 线索｜{title}",
                "digest": (
                    f"发现来源：{domain}。该条仅作全球事件线索，"
                    "尚未对原文进行独立核验，不用于自动推送。"
                ),
                "link": link,
                "time_str": published_at.strftime("%H:%M"),
                "datetime": published_at,
                "source": source_name,
                "category": "overseas",
                "importance": "low",
                "market_scope": "全球",
                "discovery_only": True,
            }
        )
        if len(items) >= settings.GDELT_DISCOVERY_MAX_RECORDS:
            break

    record_data_source_health(source_name, "success", "", len(items))
    log_info(f"GDELT 线索抓取成功: returned_count={len(items)}")
    return items


def _fetch_second_batch_news(minutes_lookback: Optional[int]) -> list[dict[str, Any]]:
    """Collect dedicated disclosures and discovery leads with explicit health logs."""
    items = _fetch_sec_edgar_filings(minutes_lookback)
    if settings.CSRC_NEWS_ENABLED:
        items.extend(
            _fetch_cn_official_news(
                source_name="中国证监会",
                feed_url=CSRC_NEWS_URL,
                material_terms=CSRC_MATERIAL_TERMS,
                minutes_lookback=minutes_lookback,
            )
        )
    else:
        record_data_source_health("中国证监会", "skipped", "未启用", 0)
    if settings.SSE_ANNOUNCEMENTS_ENABLED:
        items.extend(
            _fetch_cn_official_news(
                source_name="上海证券交易所",
                feed_url=SSE_ANNOUNCEMENTS_URL,
                material_terms=SSE_MATERIAL_TERMS,
                minutes_lookback=minutes_lookback,
            )
        )
    else:
        record_data_source_health("上海证券交易所", "skipped", "未启用", 0)
    items.extend(_fetch_gdelt_discovery_news(minutes_lookback))
    return items
