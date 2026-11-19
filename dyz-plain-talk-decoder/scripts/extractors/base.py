"""提取器公共模块：统一接口、结果构造、HTTP 封装、拦截识别与内容校验。

所有图文提取器返回前，必须经过 `validate_content`。
这里集中实现产品红线：验证页和残缺正文不得标记为 success；
遇到 401/403/429、验证码、登录跳转不重试。
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from urllib.parse import urlparse

# 超时与预算（AGENTS.md 第 7 节、PRD 反爬与稳定性策略第 2 条）
CONNECT_TIMEOUT = 5  # 连接超时（秒）
READ_TIMEOUT = 15  # 读取超时（秒）
TOTAL_BUDGET = 20  # 图文抓取总预算（秒）
MAX_RETRY = 1  # 只对网络抖动类错误重试 1 次

# 正文长度阈值：低于它视为残缺（按字符数，中英文统一）
MIN_CONTENT_CHARS = 200
# 正文明显偏短但还有内容时，视为 partial 的下限
MIN_PARTIAL_CHARS = 40

# 标准请求头：只使用普通浏览器的公开请求头，不伪造设备指纹、不带 Cookie
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 拦截特征词：命中任一，说明拿到的很可能是验证页或登录页，而不是正文
BLOCK_KEYWORDS = [
    "环境异常",
    "完成验证",
    "安全验证",
    "请完成下方验证",
    "拖动滑块",
    "点击验证",
    "人机验证",
    "验证码",
    "登录后查看",
    "登录后可见",
    "请登录后",
    "扫码登录",
    "该内容已被发布者删除",
    "此内容因违规无法查看",
    "访问过于频繁",
    "访问太频繁",
    "请稍后再试",
    "Just a moment",
    "Checking your browser",
    "Access denied",
    "Enable JavaScript and cookies to continue",
    "verify you are human",
    "captcha",
]

# 最终地址里出现这些片段，视为被跳转到登录页或验证页
LOGIN_URL_HINTS = [
    "login",
    "passport",
    "signin",
    "sign_in",
    "captcha",
    "verify",
    "wappoc_appmsgcaptcha",
    "authorize",
]

# error_reason 只使用 PRD 5.1 枚举内的取值：
# login_wall / anti_scrape / not_found / missing_dependency / asr_not_configured / network
# 下面 USER_HINTS 的键是“话术名”，empty、unsupported、partial 只是话术名，不是原因代码。
# 状态码到失败原因的映射
STATUS_REASON = {
    401: "login_wall",
    403: "anti_scrape",
    429: "anti_scrape",
    404: "not_found",
    410: "not_found",
}

# 失败原因对应的用户指引（用普通人能照做的话写）
USER_HINTS = {
    "login_wall": "这个页面需要登录才能看。请把正文复制后粘贴给我。",
    "anti_scrape": "这个网站拦住了自动读取（出现了验证或访问限制）。请把正文复制后粘贴给我，我马上帮你看。",
    "not_found": "这个链接打不开，可能已被删除或地址有误。请检查链接，或把正文复制后粘贴给我。",
    "network": "网络连接不稳定或网站没有响应。可以稍后再发一次链接，或直接把正文复制后粘贴给我。",
    "missing_dependency": "缺少抓取所需的组件：{detail}。可以先把正文复制后粘贴给我，不影响使用。",
    "unsupported": "这个类型的链接这个版本还不能自动读取。请把正文或字幕文字复制后粘贴给我。",
    "partial": "只读到了正文的一部分。请把全文复制后粘贴给我，否则结论可能不完整。",
    "empty": "页面里没有读到正文，可能内容是靠脚本动态加载的。请把正文复制后粘贴给我。",
}


def log(message: str) -> None:
    """日志一律走标准错误，避免污染标准输出里的 JSON。"""
    print(message, file=sys.stderr)


@dataclass
class Fetched:
    """一次 HTTP 请求的结果。出错时 `error_reason` 非空。"""

    final_url: str = ""
    status_code: int = 0
    text: str = ""
    error_reason: str = ""
    detail: str = ""


class Extractor(Protocol):
    """提取器统一接口。每个平台一个模块，提取器之间不互相导入。"""

    #: 本提取器负责的平台代号
    platform: str

    def can_handle(self, url: str) -> bool:
        """是否由本提取器处理该地址（按域名判断）。"""
        ...

    def extract(self, url: str, deadline: float) -> dict[str, Any]:
        """抓取并返回符合 PRD 5.1 契约的字典。`deadline` 为 time.monotonic() 的截止时刻。"""
        ...


@dataclass
class ResultBuilder:
    """构造符合 PRD 5.1 契约的结果字典，字段齐全、类型固定。"""

    platform: str = "web"
    source_type: str = "article"
    url: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def build(
        self,
        status: str,
        *,
        title: str = "",
        author: str = "",
        publish_date: str = "",
        content_text: str = "",
        extraction_method: str = "",
        error_reason: str = "",
        user_hint: str = "",
    ) -> dict[str, Any]:
        return make_result(
            status=status,
            source_type=self.source_type,
            platform=self.platform,
            url=self.url,
            title=title,
            author=author,
            publish_date=publish_date,
            content_text=content_text,
            extraction_method=extraction_method,
            error_reason=error_reason,
            user_hint=user_hint,
        )


def make_result(
    status: str,
    *,
    source_type: str = "unknown",
    platform: str = "web",
    url: str = "",
    title: str = "",
    author: str = "",
    publish_date: str = "",
    content_text: str = "",
    extraction_method: str = "",
    keyframes: Optional[list[str]] = None,
    error_reason: str = "",
    user_hint: str = "",
    duration_sec: Optional[float] = None,
    funnel_level: str = "",
    needs_confirmation: str = "",
) -> dict[str, Any]:
    """生成统一输出契约（PRD 5.1）。所有字段都出现，缺失的用空值。"""
    return {
        "status": status,
        "source_type": source_type,
        "platform": platform,
        "url": url,
        "title": title,
        "author": author,
        "publish_date": publish_date,
        "content_text": content_text,
        "extraction_method": extraction_method,
        "keyframes": keyframes or [],
        "error_reason": error_reason,
        "user_hint": user_hint,
        "duration_sec": duration_sec,
        "funnel_level": funnel_level,
        "needs_confirmation": needs_confirmation,
    }


def failed_result(
    platform: str,
    url: str,
    error_reason: str,
    *,
    source_type: str = "unknown",
    detail: str = "",
    user_hint: str = "",
) -> dict[str, Any]:
    """构造失败结果。`user_hint` 缺省时按原因代码取标准话术。"""
    hint = user_hint or USER_HINTS.get(error_reason, USER_HINTS["anti_scrape"])
    if "{detail}" in hint:
        hint = hint.format(detail=detail or "未知组件")
    return make_result(
        "failed",
        source_type=source_type,
        platform=platform,
        url=url,
        error_reason=error_reason,
        user_hint=hint,
    )


# ---------------------------------------------------------------- HTTP 封装


def http_get(url: str, deadline: float, headers: Optional[dict[str, str]] = None) -> Fetched:
    """带超时与重试规则的 GET。

    规则：连接 5 秒、读取 15 秒；总预算由 deadline 控制；
    只对网络抖动（连接错误、超时）重试 1 次；
    401/403/429、验证码、登录跳转不重试。
    `requests` 延迟导入，缺失时返回 missing_dependency。
    """
    try:
        import requests  # 延迟导入
    except ImportError:
        return Fetched(error_reason="missing_dependency", detail="requests（运行 pip install requests）")

    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    last_error = ""
    for attempt in range(MAX_RETRY + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return Fetched(error_reason="network", detail="超过总时间预算")
        connect = min(CONNECT_TIMEOUT, remaining)
        read = min(READ_TIMEOUT, max(remaining - connect, 1))
        try:
            resp = requests.get(
                url,
                headers=merged,
                timeout=(connect, read),
                allow_redirects=True,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = type(exc).__name__
            log(f"[http_get] 第 {attempt + 1} 次网络错误：{last_error}")
            continue  # 只有网络抖动才进入下一次重试
        except requests.RequestException as exc:
            return Fetched(error_reason="network", detail=type(exc).__name__)

        code = resp.status_code
        if code in STATUS_REASON:
            # 401/403/429/404 不重试
            return Fetched(
                final_url=resp.url,
                status_code=code,
                error_reason=STATUS_REASON[code],
                detail=f"HTTP {code}",
            )
        if code >= 500:
            last_error = f"HTTP {code}"
            log(f"[http_get] 第 {attempt + 1} 次服务端错误：{code}")
            continue
        if code >= 400:
            return Fetched(
                final_url=resp.url,
                status_code=code,
                error_reason="anti_scrape",
                detail=f"HTTP {code}",
            )

        # 编码：优先使用响应声明的编码，缺省时按内容猜测，避免中文乱码
        if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            resp.encoding = resp.apparent_encoding or "utf-8"
        return Fetched(final_url=resp.url, status_code=code, text=resp.text)

    return Fetched(error_reason="network", detail=last_error or "请求失败")


# ------------------------------------------------------- 拦截识别与内容校验


def looks_like_login_url(url: str) -> bool:
    """最终地址是否像登录页或验证页。"""
    parsed = urlparse(url.lower())
    target = parsed.netloc + parsed.path + ("?" + parsed.query if parsed.query else "")
    return any(hint in target for hint in LOGIN_URL_HINTS)


def find_block_keyword(text: str) -> str:
    """正文是否命中拦截特征词，命中则返回该词，否则返回空串。"""
    lowered = text.lower()
    for word in BLOCK_KEYWORDS:
        if word.lower() in lowered:
            return word
    return ""


def validate_content(
    *,
    requested_url: str,
    final_url: str,
    title: str,
    content_text: str,
    platform: str,
    source_type: str = "article",
    author: str = "",
    publish_date: str = "",
    extraction_method: str = "web_fetch",
    min_chars: int = MIN_CONTENT_CHARS,
    page_text: str = "",
) -> dict[str, Any]:
    """统一的拦截识别与内容校验。所有图文提取器返回前都必须调用它。

    `page_text` 是整页的可见文字。登录页、验证页往往没有可提取的正文，
    只有页面上的提示语，所以正文很短时也用它来识别拦截页。

    依次检查：① 最终地址是否被跳到登录/验证页；② 正文是否命中拦截特征词；
    ③ 正文长度是否过短；④ 标题与正文是否都拿到。
    任一不通过都返回 failed 或 partial，**残缺内容不会被标记为 success**。
    （第 2 条“状态码是否为 401/403/429”已在 http_get 里处理。）
    """
    base = {
        "source_type": source_type,
        "platform": platform,
        "url": final_url or requested_url,
    }
    text = (content_text or "").strip()
    title = (title or "").strip()

    # ① 登录/验证页跳转（只有地址确实变了才算跳转，用户本来就要看的登录相关文章不误伤）
    if final_url and final_url != requested_url and looks_like_login_url(final_url):
        if not looks_like_login_url(requested_url):
            return failed_result(
                platform, base["url"], "login_wall", source_type=source_type
            )

    # ② 拦截特征词：只在正文偏短时才按拦截页处理。
    # 真实的验证页、登录页正文都很短；长文章里出现“验证码”“请稍后再试”等字样很正常
    # （比如讲验证码的科普文，标题里就有这个词），所以长正文不看关键词，标题也不单独作为依据。
    hit = ""
    if len(text) < 1500:
        hit = (
            find_block_keyword(text)
            or find_block_keyword(title)
            or find_block_keyword((page_text or "")[:3000])
        )
    if hit:
        log(f"[validate] 命中拦截特征词：{hit}")
        reason = "login_wall" if "登录" in hit or "login" in hit.lower() else "anti_scrape"
        return failed_result(platform, base["url"], reason, source_type=source_type)

    # ③ 正文为空：可能是 JS 动态加载
    if not text:
        return failed_result(
            platform, base["url"], "anti_scrape",
            source_type=source_type, user_hint=USER_HINTS["empty"],
        )

    builder = ResultBuilder(platform=platform, source_type=source_type, url=base["url"])

    # ④ 标题与正文是否都拿到、正文是否太短
    n = len(text)
    if n < MIN_PARTIAL_CHARS:
        return failed_result(
            platform, base["url"], "anti_scrape",
            source_type=source_type, user_hint=USER_HINTS["empty"],
        )
    if n < min_chars or not title:
        return builder.build(
            "partial",
            title=title,
            author=author,
            publish_date=publish_date,
            content_text=text,
            extraction_method=extraction_method,
            error_reason="anti_scrape",
            user_hint=USER_HINTS["partial"],
        )

    return builder.build(
        "success",
        title=title,
        author=author,
        publish_date=publish_date,
        content_text=text,
        extraction_method=extraction_method,
    )


def clean_text(text: str) -> str:
    """规整空白：合并多余空行和空格，保留段落。"""
    text = text.replace(" ", " ").replace("​", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def host_of(url: str) -> str:
    """取小写域名（不含端口）。"""
    return (urlparse(url).hostname or "").lower()
