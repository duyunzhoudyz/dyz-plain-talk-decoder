"""通用网页提取器：普通网页、博客、新闻（T1）。

用 readability 抽正文，用 BeautifulSoup 补标题、作者、发布时间。
页面靠 JS 动态加载、抽不到正文时，如实返回失败，引导用户粘贴（首期不引入无头浏览器）。
"""

from __future__ import annotations

import re
from typing import Any

from .base import (
    Fetched,
    clean_text,
    failed_result,
    http_get,
    validate_content,
)


class WebExtractor:
    platform = "web"

    def can_handle(self, url: str) -> bool:
        # 通用网页是兜底提取器，未知域名都走它
        return True

    def extract(self, url: str, deadline: float) -> dict[str, Any]:
        fetched = http_get(url, deadline)
        if fetched.error_reason:
            return failed_result(
                self.platform, fetched.final_url or url, fetched.error_reason,
                detail=fetched.detail,
            )
        return parse_web_html(url, fetched)


def parse_web_html(requested_url: str, fetched: Fetched) -> dict[str, Any]:
    """解析网页 HTML。独立成函数，方便用离线样本做测试。"""
    try:
        from bs4 import BeautifulSoup  # 延迟导入
        from readability import Document
    except ImportError:
        return failed_result(
            "web", requested_url, "missing_dependency",
            detail="beautifulsoup4、readability-lxml（运行 pip install -r requirements.txt）",
        )

    html = fetched.text
    soup = BeautifulSoup(html, "html.parser")

    title = _pick_title(soup)
    author = _pick_meta(soup, ["author", "article:author", "og:article:author"])
    publish_date = _pick_meta(
        soup, ["article:published_time", "og:article:published_time", "pubdate", "publishdate", "date"]
    )

    content = ""
    try:
        doc = Document(html)
        summary_html = doc.summary(html_partial=True)
        content = clean_text(BeautifulSoup(summary_html, "html.parser").get_text("\n"))
        if not title:
            title = (doc.short_title() or "").strip()
    except Exception:  # readability 对畸形页面可能抛各种异常，统一降级
        content = ""

    # 正文和 <title> 一样（readability 抽不到东西时的常见表现），视为没有正文
    if content and title and content.strip() == title.strip():
        content = ""

    return validate_content(
        requested_url=requested_url,
        final_url=fetched.final_url,
        title=title,
        content_text=content,
        platform="web",
        source_type="article",
        author=author,
        publish_date=publish_date,
        extraction_method="web_fetch",
    )


def _pick_title(soup: Any) -> str:
    for key in ("og:title", "twitter:title"):
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return tag["content"].strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def _pick_meta(soup: Any, names: list[str]) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return re.sub(r"\s+", " ", tag["content"]).strip()
    return ""
