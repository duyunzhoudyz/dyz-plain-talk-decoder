"""图文抓取的离线测试：不联网，全部用 tests/fixtures 里的样本和假响应。

运行：.venv/Scripts/python.exe -m pytest tests -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "dyz-plain-talk-decoder" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import fetch_content as fc  # noqa: E402
from extractors import base  # noqa: E402
from extractors.base import Fetched  # noqa: E402
from extractors.social import SocialExtractor, parse_social_html  # noqa: E402
from extractors.web import WebExtractor, parse_web_html  # noqa: E402
from extractors.wechat import WechatExtractor, parse_wechat_html  # noqa: E402

CONTRACT_KEYS = {
    "status", "source_type", "platform", "url", "title", "author", "publish_date",
    "content_text", "extraction_method", "keyframes", "error_reason", "user_hint",
    "duration_sec", "funnel_level", "needs_confirmation",
}
ERROR_REASONS = {
    "", "login_wall", "anti_scrape", "not_found", "missing_dependency",
    "asr_not_configured", "network",
}


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(name: str, url: str = "https://example.com/a/1") -> Fetched:
    return Fetched(final_url=url, status_code=200, text=load(name))


def assert_contract(result: dict) -> None:
    assert set(result) == CONTRACT_KEYS
    assert result["status"] in ("success", "partial", "failed")
    assert result["error_reason"] in ERROR_REASONS
    json.dumps(result, ensure_ascii=False)  # 必须能序列化


# ------------------------------------------------------------------ 通用网页


class TestWeb:
    def test_normal_page_succeeds(self):
        r = parse_web_html("https://example.com/a/1", fetched("web_normal.html"))
        assert_contract(r)
        assert r["status"] == "success"
        assert r["title"] == "为什么我们把数据库从单机迁到了集群"
        assert r["author"] == "墨石科技后端团队"
        assert r["publish_date"].startswith("2025-03-18")
        assert "40 分钟的写入故障" in r["content_text"]
        assert "不建议你这么做" in r["content_text"]
        assert "版权所有" not in r["content_text"]  # 页脚被去掉

    @pytest.mark.parametrize("name", ["web_blocked.html", "web_blocked_zh.html"])
    def test_blocked_page_is_never_success(self, name):
        r = parse_web_html("https://example.com/a/1", fetched(name))
        assert_contract(r)
        assert r["status"] == "failed"
        assert r["error_reason"] in ("anti_scrape", "login_wall")
        assert r["content_text"] == ""
        assert r["user_hint"]

    def test_truncated_page_is_not_success(self):
        r = parse_web_html("https://example.com/a/1", fetched("web_truncated.html"))
        assert_contract(r)
        assert r["status"] in ("partial", "failed")  # 只要不是 success
        assert r["status"] != "success"

    def test_js_only_page_fails_with_paste_hint(self):
        r = parse_web_html("https://example.com/a/1", fetched("web_js_only.html"))
        assert_contract(r)
        assert r["status"] == "failed"
        assert "粘贴" in r["user_hint"]

    def test_long_article_mentioning_captcha_is_not_misjudged(self):
        """讲验证码的科普长文，正文里有“验证码”三个字，不能被误判成验证页。"""
        r = parse_web_html("https://example.com/a/1", fetched("web_long_article_with_captcha_word.html"))
        assert r["status"] == "success", r
        assert "验证码" in r["content_text"]


# ------------------------------------------------------------------ 公众号


class TestWechat:
    URL = "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1&sn=xyz"

    def test_normal_article_succeeds(self):
        r = parse_wechat_html(self.URL, fetched("wechat_normal.html", self.URL))
        assert_contract(r)
        assert r["status"] == "success"
        assert r["platform"] == "wechat"
        assert r["title"] == "东岭市这三项新规，下个月起影响你的出行"
        assert r["author"] == "东岭观察"
        assert r["publish_date"] == "2025-03-18 10:10"  # 1742263800 的北京时间（UTC+8）
        assert "公交专用道" in r["content_text"]
        assert "被隐藏的推广文字" not in r["content_text"]

    def test_blocked_page_fails(self):
        r = parse_wechat_html(self.URL, fetched("wechat_blocked.html", self.URL))
        assert_contract(r)
        assert r["status"] == "failed"
        assert r["error_reason"] == "anti_scrape"

    def test_truncated_article_is_partial_not_success(self):
        r = parse_wechat_html(self.URL, fetched("wechat_truncated.html", self.URL))
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["status"] != "success"
        assert r["content_text"]  # 已读到的部分要保留，让 Agent 能说明只读到了哪里

    def test_redirect_to_captcha_page_is_login_wall(self):
        f = Fetched(
            final_url="https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?x=1",
            status_code=200,
            text=load("wechat_normal.html"),  # 即使内容看着正常，被跳到验证页就不可信
        )
        r = parse_wechat_html(self.URL, f)
        assert r["status"] == "failed"
        assert r["error_reason"] == "login_wall"


# ------------------------------------------------------------------ 社媒


class TestSocial:
    URL = "https://weibo.com/123/abc"

    def test_normal_page_is_at_best_partial(self):
        """meta 摘要只是开头片段，不能当全文：即使文字足够，也最多是 partial。"""
        r = parse_social_html(self.URL, fetched("social_normal.html", self.URL), "weibo")
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["platform"] == "weibo"
        assert r["source_type"] == "social_post"
        assert "公交专用道" in r["content_text"]
        assert "粘贴" in r["user_hint"]

    def test_login_page_fails(self):
        r = parse_social_html(self.URL, fetched("social_blocked.html", self.URL), "weibo")
        assert_contract(r)
        assert r["status"] == "failed"
        assert r["error_reason"] == "login_wall"

    def test_truncated_never_success(self):
        r = parse_social_html(self.URL, fetched("social_truncated.html", self.URL), "weibo")
        assert_contract(r)
        assert r["status"] != "success"


# ------------------------------------------------------------------ 输入归一化


class TestNormalize:
    def test_extract_url_from_douyin_share_text(self):
        raw = "3.00 复制打开抖音，看看【某作品】 https://v.douyin.com/AbC123/ 06/12 a@b.cn"
        assert fc.extract_url(raw) == "https://v.douyin.com/AbC123/"

    def test_extract_url_strips_trailing_chinese_punctuation(self):
        assert fc.extract_url("看这篇：https://example.com/a/1，不错") == "https://example.com/a/1"
        assert fc.extract_url("链接（https://example.com/a/1）") == "https://example.com/a/1"

    def test_extract_url_without_scheme(self):
        assert fc.extract_url("example.com/post/1") == "https://example.com/post/1"

    def test_no_url(self):
        assert fc.extract_url("这里没有任何链接") == ""
        assert fc.extract_url("") == ""

    def test_strip_tracking_params(self):
        u = fc.strip_tracking("https://example.com/a?id=5&utm_source=x&fbclid=z&spm=1")
        assert u == "https://example.com/a?id=5"

    def test_strip_tracking_keeps_functional_params(self):
        # version=2 在普通站点里可能是功能参数，不能误删
        assert fc.strip_tracking("https://example.com/a?id=5&version=2") == "https://example.com/a?id=5&version=2"

    def test_wechat_keeps_locating_params_drops_tracking(self):
        u = fc.strip_tracking(
            "https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1&sn=abc&scene=21&clicktime=1&pass_ticket=SECRET#wechat_redirect"
        )
        assert "__biz=MzA" in u and "mid=1" in u and "idx=1" in u and "sn=abc" in u
        assert "scene" not in u and "clicktime" not in u
        assert "pass_ticket" not in u  # 会话凭据不能留在地址里
        assert u.endswith("#wechat_redirect")

    def test_short_link_resolution_failure_returns_original(self, monkeypatch):
        """短链解析失败不致命：返回原地址，由后续步骤给出失败结果。"""
        import requests

        def boom(*a, **k):
            raise requests.ConnectionError("no network")

        monkeypatch.setattr(requests, "get", boom)
        assert fc.resolve_short_url("https://b23.tv/xyz", time.monotonic() + 20) == "https://b23.tv/xyz"

    def test_non_short_link_is_not_requested(self, monkeypatch):
        import requests

        def should_not_call(*a, **k):
            raise AssertionError("非短链不应发起请求")

        monkeypatch.setattr(requests, "get", should_not_call)
        assert fc.resolve_short_url("https://example.com/a", time.monotonic() + 20) == "https://example.com/a"


# ------------------------------------------------------------------ 路由


class TestRouting:
    @pytest.mark.parametrize(
        "url,cls",
        [
            ("https://mp.weixin.qq.com/s/abc", WechatExtractor),
            ("https://weibo.com/1/2", SocialExtractor),
            ("https://m.weibo.cn/detail/1", SocialExtractor),
            ("https://x.com/u/status/1", SocialExtractor),
            ("https://www.xiaohongshu.com/explore/1", SocialExtractor),
            ("https://example.com/news/1", WebExtractor),
            ("https://some-unknown-site.org/x", WebExtractor),
        ],
    )
    def test_route(self, url, cls):
        assert isinstance(fc.route(url), cls)

    def test_lookalike_domain_is_not_routed_to_wechat(self):
        assert isinstance(fc.route("https://mp.weixin.qq.com.evil.example/s/1"), WebExtractor)

    @pytest.mark.parametrize(
        "url,platform",
        [
            ("https://www.bilibili.com/video/BV1xx", "bilibili"),
            ("https://www.youtube.com/watch?v=1", "youtube"),
            ("https://youtu.be/1", "youtube"),
            ("https://www.douyin.com/video/1", "douyin"),
        ],
    )
    def test_video_platforms_detected(self, url, platform):
        assert fc.video_platform_of(url) == platform

    def test_video_link_missing_ytdlp_fails_honestly(self, monkeypatch):
        # Phase 3：视频走漏斗；缺少 yt-dlp 时不联网、不脑补，诚实返回失败与指引
        import sys

        monkeypatch.setitem(sys.modules, "yt_dlp", None)  # 让 import yt_dlp 抛 ImportError
        r = fc.fetch("https://www.bilibili.com/video/BV1xx")
        assert_contract(r)
        assert r["status"] == "failed"
        assert r["source_type"] == "video"
        assert r["error_reason"] == "missing_dependency"
        assert r["content_text"] == ""
        assert "yt-dlp" in r["user_hint"]


# ------------------------------------------------------------------ HTTP 规则


class FakeResp:
    def __init__(self, code=200, text="<html></html>", url="https://example.com/a"):
        self.status_code, self.text, self.url = code, text, url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


class TestHttpRules:
    def _patch(self, monkeypatch, behaviours):
        import requests

        calls = []

        def fake_get(url, **kw):
            calls.append(kw)
            b = behaviours[min(len(calls) - 1, len(behaviours) - 1)]
            if isinstance(b, Exception):
                raise b
            return b

        monkeypatch.setattr(requests, "get", fake_get)
        return calls

    @pytest.mark.parametrize("code,reason", [(401, "login_wall"), (403, "anti_scrape"), (429, "anti_scrape"), (404, "not_found")])
    def test_blocking_status_codes_are_not_retried(self, monkeypatch, code, reason):
        calls = self._patch(monkeypatch, [FakeResp(code)])
        f = base.http_get("https://example.com/a", time.monotonic() + 20)
        assert f.error_reason == reason
        assert len(calls) == 1  # 一次就停，不重试

    def test_network_error_retries_exactly_once(self, monkeypatch):
        import requests

        calls = self._patch(monkeypatch, [requests.ConnectionError("x")])
        f = base.http_get("https://example.com/a", time.monotonic() + 20)
        assert f.error_reason == "network"
        assert len(calls) == 2  # 首次 + 重试 1 次

    def test_retry_recovers_after_one_blip(self, monkeypatch):
        import requests

        calls = self._patch(monkeypatch, [requests.Timeout("x"), FakeResp(200, "<p>ok</p>")])
        f = base.http_get("https://example.com/a", time.monotonic() + 20)
        assert not f.error_reason and f.text == "<p>ok</p>"
        assert len(calls) == 2

    def test_timeouts_are_set_and_within_limits(self, monkeypatch):
        calls = self._patch(monkeypatch, [FakeResp(200)])
        base.http_get("https://example.com/a", time.monotonic() + 20)
        connect, read = calls[0]["timeout"]
        assert connect <= base.CONNECT_TIMEOUT and read <= base.READ_TIMEOUT

    def test_exhausted_budget_returns_without_requesting(self, monkeypatch):
        calls = self._patch(monkeypatch, [FakeResp(200)])
        f = base.http_get("https://example.com/a", time.monotonic() - 1)
        assert f.error_reason == "network"
        assert calls == []

    def test_no_cookie_header_is_ever_sent(self, monkeypatch):
        calls = self._patch(monkeypatch, [FakeResp(200)])
        base.http_get("https://example.com/a", time.monotonic() + 20)
        assert "cookie" not in {k.lower() for k in calls[0]["headers"]}


# ------------------------------------------------------------------ 整体兜底


class TestFetchEndToEndOffline:
    def test_no_link(self):
        r = fc.fetch("纯文字，没有链接")
        assert_contract(r)
        assert r["status"] == "failed" and r["user_hint"]

    def test_unexpected_exception_still_returns_valid_json(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("内部错误 SECRET-TRACE")

        monkeypatch.setattr(fc, "route", boom)
        r = fc.fetch("https://example.com/a")
        assert_contract(r)
        assert r["status"] == "failed"
        assert "SECRET-TRACE" not in json.dumps(r, ensure_ascii=False)  # 不向用户暴露堆栈/内部信息

    def test_extractor_failure_is_structured(self, monkeypatch):
        monkeypatch.setattr(base, "http_get", lambda *a, **k: Fetched(error_reason="anti_scrape", detail="HTTP 403"))
        import extractors.web as web_mod

        monkeypatch.setattr(web_mod, "http_get", lambda *a, **k: Fetched(error_reason="anti_scrape", detail="HTTP 403"))
        r = fc.fetch("https://example.com/a")
        assert_contract(r)
        assert r["status"] == "failed" and r["error_reason"] == "anti_scrape"
        assert "粘贴" in r["user_hint"]

    def test_cli_outputs_single_utf8_json_with_exit_zero(self):
        """作为命令行运行：标准输出只有一份 JSON，中文不乱码，退出码 0。"""
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "fetch_content.py"), "只有文字没有链接"],
            capture_output=True, timeout=30,
        )
        assert proc.returncode == 0
        out = proc.stdout.decode("utf-8")
        data = json.loads(out)  # 整个标准输出必须是单个 JSON
        assert_contract(data)
        assert "链接" in data["user_hint"]  # 中文完整，没有乱码

    def test_cli_without_arguments_still_outputs_json(self):
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "fetch_content.py")],
            capture_output=True, timeout=30,
        )
        assert proc.returncode == 0
        assert_contract(json.loads(proc.stdout.decode("utf-8")))


class TestCheckEnv:
    def test_runs_and_outputs_json(self):
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "check_env.py")],
            capture_output=True, timeout=60,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout.decode("utf-8"))
        assert data["capabilities"]["paste_text"] is True
        assert "user_hint" in data

    def test_never_prints_secret(self, monkeypatch):
        sys.path.insert(0, str(SCRIPTS))
        import check_env

        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "sk-SUPER-SECRET-VALUE")
        out = json.dumps(check_env.run_checks(), ensure_ascii=False)
        assert "SUPER-SECRET" not in out

    def test_works_when_optional_deps_are_missing(self, monkeypatch):
        sys.path.insert(0, str(SCRIPTS))
        import check_env

        monkeypatch.setattr(check_env, "check_module", lambda name: None)
        monkeypatch.setattr(check_env.shutil, "which", lambda name: None)
        data = check_env.run_checks()
        assert data["capabilities"]["paste_text"] is True
        assert data["capabilities"]["fetch_articles"] is False
        assert "pip install" in data["user_hint"]

    def test_ytdlp_version_read_from_submodule(self, monkeypatch):
        """yt_dlp 顶层没有 __version__；应从 yt_dlp.version 子模块取，过旧提示才能生效。"""
        sys.path.insert(0, str(SCRIPTS))
        import check_env

        def fake_check(name):
            return {"yt_dlp": "unknown", "yt_dlp.version": "2020.01.01"}.get(name)

        monkeypatch.setattr(check_env, "check_module", fake_check)
        monkeypatch.setattr(check_env.shutil, "which", lambda name: None)
        data = check_env.run_checks()
        item = next(i for i in data["optional"] if i["name"] == "yt-dlp")
        assert item["ok"] is True
        assert item["version"] == "2020.01.01"
        assert "pip install -U yt-dlp" in item["hint"]  # 过旧版本给出升级提示

    def test_cli_only_ytdlp_is_not_ok(self, monkeypatch):
        """只有命令行 yt-dlp 可执行文件、没有 Python 库时不能报 ok。

        视频脚本通过 import yt_dlp 使用库；P4-05 全新环境实测发现旧版
        check_env 回退检测命令行程序会误报 video_subtitles=true。
        """
        sys.path.insert(0, str(SCRIPTS))
        import check_env

        monkeypatch.setattr(check_env, "check_module", lambda name: None)
        monkeypatch.setattr(
            check_env.shutil,
            "which",
            lambda name: "/usr/bin/yt-dlp" if name == "yt-dlp" else None,
        )
        data = check_env.run_checks()
        item = next(i for i in data["optional"] if i["name"] == "yt-dlp")
        assert item["ok"] is False
        assert "pip install yt-dlp" in item["hint"]
        assert "Python 库" in item["hint"]
        assert data["capabilities"]["video_subtitles"] is False


class TestVeryShortContent:
    def test_tiny_body_is_never_success(self):
        """正文只有一两句（远低于下限）时，不能标记 success，也不能当成有内容。"""
        url = "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1&sn=xyz"
        html = (
            '<html><body><h1 id="activity-name">标题</h1>'
            '<div id="js_content"><p>东岭市发布通知。</p></div></body></html>'
        )
        r = parse_wechat_html(url, Fetched(final_url=url, status_code=200, text=html))
        assert r["status"] == "failed"
        assert r["content_text"] == ""
