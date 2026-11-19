"""微信公众号提取器：公开文章（T1）。

公众号文章的标题、作者、发布时间、正文都有固定的页面结构：
标题在 #activity-name，公众号名在 #js_name，正文在 #js_content。
被风控时，页面会返回“环境异常/完成验证”之类的验证页；这时立即停止，不重试。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any

from .base import (
    Fetched,
    clean_text,
    failed_result,
    host_of,
    http_get,
    validate_content,
)

WECHAT_HOSTS = ("mp.weixin.qq.com",)


class WechatExtractor:
    platform = "wechat"

    def can_handle(self, url: str) -> bool:
        return host_of(url) in WECHAT_HOSTS

    def extract(self, url: str, deadline: float) -> dict[str, Any]:
        fetched = http_get(url, deadline)
        if fetched.error_reason:
            return failed_result(
                self.platform, fetched.final_url or url, fetched.error_reason,
                detail=fetched.detail,
            )
        return parse_wechat_html(url, fetched)


def parse_wechat_html(requested_url: str, fetched: Fetched) -> dict[str, Any]:
    """解析公众号文章 HTML。独立成函数，方便用离线样本做测试。"""
    try:
        from bs4 import BeautifulSoup  # 延迟导入
    except ImportError:
        return failed_result(
            "wechat", requested_url, "missing_dependency",
            detail="beautifulsoup4（运行 pip install -r requirements.txt）",
        )

    soup = BeautifulSoup(fetched.text, "html.parser")

    title_tag = soup.find(id="activity-name") or soup.find("meta", attrs={"property": "og:title"})
    title = ""
    if title_tag is not None:
        title = (title_tag.get("content") if title_tag.name == "meta" else title_tag.get_text(strip=True)) or ""

    author_tag = soup.find(id="js_name")
    author = author_tag.get_text(strip=True) if author_tag else ""

    content_tag = soup.find(id="js_content")
    content = ""
    if content_tag is not None:
        # 公众号正文里会有被隐藏的元素（样式 visibility:hidden），整页未渲染时需忽略
        for hidden in content_tag.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
            hidden.decompose()
        content = clean_text(content_tag.get_text("\n"))

    publish_date = _extract_publish_date(fetched.text)

    return validate_content(
        requested_url=requested_url,
        final_url=fetched.final_url,
        title=title,
        content_text=content,
        platform="wechat",
        source_type="article",
        author=author,
        publish_date=publish_date,
        extraction_method="web_fetch",
    )


def _extract_publish_date(html: str) -> str:
    """公众号的发布时间藏在脚本变量 ct 里（Unix 时间戳），转成北京时间。"""
    m = re.search(r"\bct\s*=\s*['\"]?(\d{10})['\"]?", html)
    if not m:
        return ""
    ts = int(m.group(1))
    tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")
