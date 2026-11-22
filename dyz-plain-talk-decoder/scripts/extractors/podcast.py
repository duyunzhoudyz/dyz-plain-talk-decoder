"""播客公开单集提取器：小宇宙（xiaoyuzhoufm.com）。

定位为尽力而为（T2）：读取公开页面中的标题、节目名、发布时间、Shownotes
和公开音频直链线索。

产品红线：单集页面的 Shownotes 只是文字简介，不是全期逐字稿；
因此即使文字充足，本提取器最多返回 `partial`，绝不标成 `success`，
避免把几百字简介当成整期播客来解码。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import (
    Fetched,
    clean_text,
    failed_result,
    find_block_keyword,
    host_of,
    looks_like_login_url,
    make_result,
    http_get,
)

PODCAST_DOMAINS = ("xiaoyuzhoufm.com",)


class PodcastExtractor:
    platform = "xiaoyuzhou"

    def can_handle(self, url: str) -> bool:
        host = host_of(url)
        return any(host == d or host.endswith("." + d) for d in PODCAST_DOMAINS)

    def extract(self, url: str, deadline: float) -> dict[str, Any]:
        fetched = http_get(url, deadline)
        if fetched.error_reason:
            return failed_result(
                self.platform,
                fetched.final_url or url,
                fetched.error_reason,
                source_type="video",
                detail=fetched.detail,
            )
        return parse_xiaoyuzhou_html(url, fetched)


def _extract_next_episode(soup: Any) -> dict[str, Any]:
    """尝试从小宇宙页面内嵌的 __NEXT_DATA__ JSON 读取单集结构化字段。"""
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return {}
    try:
        data = json.loads(script.string)
    except (ValueError, TypeError):
        return {}
    props = data.get("props") if isinstance(data, dict) else None
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    episode = page_props.get("episode") if isinstance(page_props, dict) else None
    return episode if isinstance(episode, dict) else {}


def _meta(soup: Any, keys: list[str]) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    from bs4 import BeautifulSoup

    return clean_text(BeautifulSoup(raw, "html.parser").get_text("\n"))


def parse_xiaoyuzhou_html(requested_url: str, fetched: Fetched) -> dict[str, Any]:
    """解析小宇宙公开单集页。独立函数，便于离线样本单测。"""
    try:
        from bs4 import BeautifulSoup  # 延迟导入
    except ImportError:
        return failed_result(
            "xiaoyuzhou",
            requested_url,
            "missing_dependency",
            source_type="video",
            detail="beautifulsoup4（运行 pip install -r requirements.txt）",
        )

    final_url = fetched.final_url or requested_url
    if final_url != requested_url and looks_like_login_url(final_url):
        return failed_result("xiaoyuzhou", final_url, "login_wall", source_type="video")

    soup = BeautifulSoup(fetched.text, "html.parser")
    page_text = clean_text(soup.get_text("\n"))
    if len(page_text) < 1200:
        hit = find_block_keyword(page_text)
        if hit:
            reason = "login_wall" if "登录" in hit or "login" in hit.lower() else "anti_scrape"
            return failed_result("xiaoyuzhou", final_url, reason, source_type="video")

    ep = _extract_next_episode(soup)

    title = str(ep.get("title") or "").strip() or _meta(soup, ["og:title", "twitter:title"])
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    podcast = ep.get("podcast") if isinstance(ep.get("podcast"), dict) else {}
    author = str(podcast.get("title") or ep.get("author") or "").strip() or _meta(soup, ["author"])

    pub_raw = str(ep.get("pubDate") or "").strip() or _meta(soup, ["article:published_time"])
    publish_date = pub_raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", pub_raw) else pub_raw

    duration_sec: float | None = None
    dur = ep.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        duration_sec = float(dur)

    shownotes = ""
    if ep.get("shownotes"):
        shownotes = _strip_html(str(ep["shownotes"]))
    elif ep.get("description"):
        shownotes = clean_text(str(ep["description"]))
    if not shownotes:
        shownotes = clean_text(_meta(soup, ["og:description", "description"]))

    audio_url = ""
    media = ep.get("media") if isinstance(ep.get("media"), dict) else {}
    source = media.get("source") if isinstance(media.get("source"), dict) else {}
    enclosure = ep.get("enclosure") if isinstance(ep.get("enclosure"), dict) else {}
    audio_url = (
        str(source.get("url") or enclosure.get("url") or "").strip()
        or _meta(soup, ["og:audio", "og:audio:url"])
    )

    if not shownotes and not audio_url:
        return failed_result(
            "xiaoyuzhou",
            final_url,
            "anti_scrape",
            source_type="video",
            user_hint="没有从小宇宙页面读到单集简介或音频线索，可能页面需要登录或已下架。请贴出文字稿或提供本地音频文件。",
        )

    hint_parts = [
        "只拿到了小宇宙单集的文字简介（Shownotes），不是整期播客的逐字稿。",
        "不要把简介当成全期内容解码；若要完整解读，请提供文字稿或本地音频文件。",
    ]
    if audio_url:
        hint_parts.append(f"页面公开音频线索：{audio_url}")

    return make_result(
        "partial",
        source_type="video",
        platform="xiaoyuzhou",
        url=final_url,
        title=title,
        author=author,
        publish_date=publish_date,
        content_text=shownotes,
        extraction_method="web_fetch",
        duration_sec=duration_sec,
        funnel_level="L0",
        error_reason="",
        user_hint=" ".join(hint_parts),
    )
