#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "requests>=2.31",
#   "beautifulsoup4>=4.12",
#   "readability-lxml>=0.8.1",
# ]
# ///
"""统一抓取入口：输入归一化、按域名路由、输出标准 JSON（PRD 5.1）。

用法：
    python scripts/fetch_content.py "<链接或整段分享文本>" [--frames] [--allow-long] [--start 00:10:00 --end 00:20:00]

约定（AGENTS.md 第 7 节）：
- 标准输出只打印一份 UTF-8 JSON，日志和调试信息走标准错误；
- 只要输出了 JSON 就以退出码 0 结束，成败由 `status` 字段表达；
- 预期内的失败都转成 `error_reason` + `user_hint`，不向用户暴露堆栈。

图文：普通网页、公众号、微博/X/小红书。
视频（B站、YouTube、抖音、视频号）：走省流漏斗 L0→L1，字幕不合格且配置了 ASR 时进 L2；
`--frames` 触发 L3 抽帧（复用缓存），`--allow-long` 越过 30 分钟时长闸门，
`--start`/`--end` 只处理指定时间段。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 以脚本方式运行时（含 python -I），脚本所在目录不一定在搜索路径里，这里显式加入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extractors.base import (  # noqa: E402
    CONNECT_TIMEOUT,
    DEFAULT_HEADERS,
    TOTAL_BUDGET,
    failed_result,
    host_of,
    log,
    make_result,
)
from extractors.podcast import PodcastExtractor  # noqa: E402
from extractors.social import SocialExtractor  # noqa: E402
from extractors.video import (  # noqa: E402
    L01_BUDGET,
    LOCAL_MEDIA_EXTS,
    VIDEO_HOSTS,
    VideoExtractor,
    parse_time_spec,
)
from extractors.web import WebExtractor  # noqa: E402
from extractors.wechat import WechatExtractor  # noqa: E402

# 短链域名：需要先解析到最终地址
SHORT_LINK_HOSTS = {
    "b23.tv",
    "v.douyin.com",
    "t.cn",
    "xhslink.com",
    "youtu.be",
    "url.cn",
}

# 追踪参数：这些参数只用于统计来源，去掉不影响页面内容
TRACKING_PARAMS = {
    "spm", "spm_id_from", "from_source", "fbclid", "gclid", "yclid", "mc_cid", "mc_eid",
    "share_source", "share_medium", "share_plat", "share_session_id", "share_tag", "share_from",
    "vd_source", "buvid", "is_story_h5", "unique_k", "bbid",
}

# 只对公众号域名生效的追踪/凭据类参数。
# __biz、mid、idx、sn 是定位文章必需的，不能删；其余是分享来源与会话信息。
WECHAT_TRACKING_PARAMS = {
    "scene", "clicktime", "enterid", "ascene", "devicetype", "version", "nettype",
    "abtest_cookie", "exportkey", "pass_ticket", "wx_header", "sessionid", "subscene", "chksm",
}

URL_PATTERN = re.compile(r"https?://[^\s一-鿿<>\"'“”‘’]+", re.IGNORECASE)
TRAILING_PUNCT = "，。！？、；：,.!?;:)]）】》」』…"


# ------------------------------------------------------------ 输入归一化


def extract_url(raw: str) -> str:
    """从分享口令文本中提取第一个链接；没有链接则返回空串。

    抖音、小红书的分享内容常带一段文字，不能把整段当链接。
    """
    text = (raw or "").strip()
    match = URL_PATTERN.search(text)
    if match:
        return match.group(0).rstrip(TRAILING_PUNCT)
    # 没写协议头的单个链接，如 "example.com/a/1"
    if " " not in text and "\n" not in text and re.match(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", text, re.I):
        return "https://" + text
    return ""


def strip_tracking(url: str) -> str:
    """去掉追踪参数，保留定位内容必需的参数。"""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    drop = set(TRACKING_PARAMS)
    if (parsed.hostname or "").lower() == "mp.weixin.qq.com":
        drop |= WECHAT_TRACKING_PARAMS
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in drop and not k.lower().startswith("utm_")
    ]
    return urlunparse(parsed._replace(query=urlencode(kept)))


def resolve_short_url(url: str, deadline: float) -> str:
    """把短链解析到最终地址。失败就返回原地址，由后续步骤给出失败结果。

    只跟随跳转取最终地址，不读取正文；遵守连接 5 秒的超时与总预算。
    """
    if host_of(url) not in SHORT_LINK_HOSTS:
        return url
    try:
        import requests  # 延迟导入
    except ImportError:
        return url
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        return url
    try:
        resp = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=(min(CONNECT_TIMEOUT, remaining), min(8, remaining)),
            allow_redirects=True,
            stream=True,  # 不下载正文，只要最终地址
        )
        final = resp.url
        resp.close()
        return final or url
    except Exception as exc:  # 短链解析失败不致命
        log(f"[resolve_short_url] 解析失败：{type(exc).__name__}")
        return url


def normalize(raw: str, deadline: float) -> str:
    """分享文本 -> 干净、最终的链接。返回空串表示没找到链接。"""
    url = extract_url(raw)
    if not url:
        return ""
    url = resolve_short_url(url, deadline)
    return strip_tracking(url)


# ------------------------------------------------------------ 路由


def video_platform_of(url: str) -> str:
    host = host_of(url)
    for domain, platform in VIDEO_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return ""


def detect_local_media(raw: str) -> Optional[Path]:
    """判断输入是否为本地音视频文件路径（含路径拼错/不存在的文件，避免被误当裸域名发网）。"""
    text = (raw or "").strip().strip('"').strip("'")
    if not text or "\n" in text or re.match(r"^https?://", text, re.I):
        return None
    try:
        path = Path(text).expanduser()
        if path.suffix.lower() in LOCAL_MEDIA_EXTS:
            # 只要后缀属于音视频且像本地路径或单个文件名（不含多段空格句子），一律走本地路径分支
            if path.is_file() or any(sep in text for sep in ("/", "\\", ":")) or " " not in text:
                return path
    except OSError:
        return None
    return None


def route(url: str) -> Any:
    """按域名选提取器；未知域名走通用网页提取器。"""
    for extractor in (WechatExtractor(), PodcastExtractor(), SocialExtractor()):
        if extractor.can_handle(url):
            return extractor
    return WebExtractor()


# ------------------------------------------------------------ 主流程


def fetch(
    raw_input: str,
    budget: float = TOTAL_BUDGET,
    *,
    frames: bool = False,
    allow_long: bool = False,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
) -> dict[str, Any]:
    """完整的抓取流程。无论发生什么都返回合法结果字典，不抛异常。"""
    deadline = time.monotonic() + budget
    try:
        local_media = detect_local_media(raw_input)
        if local_media is not None:
            return VideoExtractor("local").extract_local(
                local_media,
                frames=frames,
                allow_long=allow_long,
                start_sec=start_sec,
                end_sec=end_sec,
            )

        url = normalize(raw_input, deadline)
        if not url:
            return make_result(
                "failed",
                source_type="unknown",
                platform="web",
                error_reason="not_found",
                user_hint="我没有在你发的内容里找到链接。请把链接或正文直接发给我。",
            )

        video_platform = video_platform_of(url)
        if video_platform:
            # 视频走省流漏斗；L0/L1 有独立的时间预算，L2/L3 是长任务不受它限制
            video_deadline = time.monotonic() + L01_BUDGET
            return VideoExtractor(video_platform).extract(
                url,
                video_deadline,
                frames=frames,
                allow_long=allow_long,
                start_sec=start_sec,
                end_sec=end_sec,
            )

        extractor = route(url)
        return extractor.extract(url, deadline)
    except Exception:  # 兜底：任何意外都转成结构化失败，堆栈只进日志
        log("[fetch] 未预期的错误：\n" + traceback.format_exc())
        return failed_result(
            "web",
            "",
            "network",
            user_hint="读取这个链接时出了点问题。请把正文复制后粘贴给我，我马上帮你看。",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抓取链接内容并输出标准 JSON")
    parser.add_argument("input", help="链接，或包含链接的整段分享文本")
    parser.add_argument("--frames", action="store_true", help="（视频）L3 抽帧，复用已有缓存")
    parser.add_argument("--allow-long", action="store_true", help="（视频）用户同意后越过 30 分钟时长闸门")
    parser.add_argument("--start", default="", help="（视频）起始时间，如 00:10:00")
    parser.add_argument("--end", default="", help="（视频）结束时间，如 00:20:00")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    # Windows 控制台默认编码不是 UTF-8，统一设置，避免中文乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    try:
        args = build_parser().parse_args(argv)
        try:
            start_sec = parse_time_spec(args.start) if args.start.strip() else None
            end_sec = parse_time_spec(args.end) if args.end.strip() else None
        except ValueError:
            result = failed_result(
                "web", "", "not_found",
                user_hint="时间格式没认出来。请用 00:10:00 或 10:00 这样的写法告诉我起止时间。",
            )
            start_sec = end_sec = None
            args = None
        if args is not None:
            if start_sec is not None and end_sec is not None and end_sec <= start_sec:
                result = failed_result(
                    "web", "", "not_found",
                    user_hint="结束时间要比开始时间晚。请检查一下 --start 和 --end。",
                )
            else:
                result = fetch(
                    args.input,
                    frames=args.frames,
                    allow_long=args.allow_long,
                    start_sec=start_sec,
                    end_sec=end_sec,
                )
    except SystemExit:
        # argparse 参数错误：也要输出 JSON，不让用户看到用法堆栈
        result = failed_result(
            "web", "", "not_found",
            user_hint="没有收到链接。请把链接或正文直接发给我。",
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
