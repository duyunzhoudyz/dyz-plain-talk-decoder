"""微博 / X / 小红书提取器：T2，尽力而为。

只做“用标准请求头尝试一次公开页面抓取 + 拦截识别”。这些平台多数需要登录或带强风控，
失败是常态，不视为缺陷：失败就直接返回降级指引，请用户粘贴正文。
不做任何绕过：不带 Cookie、不换代理、不重试、不伪造设备指纹。
"""

from __future__ import annotations

from typing import Any

from .base import (
    Fetched,
    clean_text,
    failed_result,
    host_of,
    http_get,
    validate_content,
)

# 域名 -> 平台代号
SOCIAL_HOSTS = {
    "weibo.com": "weibo",
    "m.weibo.cn": "weibo",
    "x.com": "x",
    "twitter.com": "x",
    "xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
}

SOCIAL_HINT = "这个平台通常需要登录才能读取。请把帖子的文字复制后粘贴给我（也可以截图发给我）。"


def platform_of(url: str) -> str:
    host = host_of(url)
    for domain, platform in SOCIAL_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return ""


class SocialExtractor:
    platform = "social"

    def can_handle(self, url: str) -> bool:
        return bool(platform_of(url))

    def extract(self, url: str, deadline: float) -> dict[str, Any]:
        platform = platform_of(url) or "web"
        fetched = http_get(url, deadline)
        if fetched.error_reason:
            return failed_result(
                platform, fetched.final_url or url, fetched.error_reason,
                source_type="social_post", detail=fetched.detail,
                user_hint=SOCIAL_HINT if fetched.error_reason != "network" else "",
            )
        result = parse_social_html(url, fetched, platform)
        if result["status"] != "success" and not result["user_hint"]:
            result["user_hint"] = SOCIAL_HINT
        return result


def parse_social_html(requested_url: str, fetched: Fetched, platform: str) -> dict[str, Any]:
    """解析社媒页面里能读到的公开文字。独立成函数，方便离线测试。"""
    try:
        from bs4 import BeautifulSoup  # 延迟导入
    except ImportError:
        return failed_result(
            platform, requested_url, "missing_dependency",
            source_type="social_post",
            detail="beautifulsoup4（运行 pip install -r requirements.txt）",
        )

    soup = BeautifulSoup(fetched.text, "html.parser")

    def meta(*names: str) -> str:
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    title = meta("og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    # 公开页面通常只在 meta 里给出帖子的前一段文字，这不是全文，所以走 validate 后多半是 partial
    description = meta("og:description", "twitter:description", "description")
    author = meta("author", "article:author")

    result = validate_content(
        requested_url=requested_url,
        final_url=fetched.final_url,
        title=title,
        content_text=clean_text(description),
        platform=platform,
        source_type="social_post",
        author=author,
        extraction_method="web_fetch",
        min_chars=60,
        page_text=soup.get_text(" ", strip=True),
    )
    # 公开页面的 meta 描述只是帖子的开头片段，可能被截断，不能当全文：
    # 即使通过了校验，也最多是 partial，避免把片段当全文去解码。
    if result["status"] == "success":
        result["status"] = "partial"
        result["error_reason"] = "anti_scrape"
        result["user_hint"] = "只读到了帖子的开头片段，可能不是全文。请把完整内容复制后粘贴给我。"
    return result
