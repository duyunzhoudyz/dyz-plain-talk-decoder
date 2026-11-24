"""视频省流漏斗的离线测试：不联网、不下载媒体，全部用样本与打桩。

运行：.venv/Scripts/python.exe -m pytest tests -q
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "dyz-plain-talk-decoder" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "subtitles"
sys.path.insert(0, str(SCRIPTS))

import fetch_content as fc  # noqa: E402
from extractors import video  # noqa: E402
from extractors.base import Fetched  # noqa: E402
from extractors.video import (  # noqa: E402
    Segment,
    VideoExtractor,
    cache_dir_for,
    check_subtitle_quality,
    format_timestamp,
    format_transcript,
    gate_decision,
    parse_bili_json,
    parse_json3,
    parse_srv3,
    parse_subtitle,
    parse_time_spec,
    parse_vtt,
    pick_subtitle_track,
)

# ffmpeg 生成的 8×8 黑色 JPEG；不依赖本机图片库，也能覆盖真实帧完整性校验。
JPEG_FRAME = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMQD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYH"
    "BwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/xABLAAEBAAAAAAAAAAAAAAAAAAAACAEBAAAAAAAAAAAA"
    "AAAAAAAAABABAAAAAAAAAAAAAAAAAAAAABEBAAAAAAAAAAAAAAAAAAAAAP/AABEIAAgACAMBIgACEQADEQD/2gAMAwEAAhEDEQ"
    "A/AJ/AB//Z"
)

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


def assert_contract(result: dict) -> None:
    assert set(result) == CONTRACT_KEYS
    assert result["status"] in ("success", "partial", "failed")
    assert result["error_reason"] in ERROR_REASONS
    json.dumps(result, ensure_ascii=False)


def make_info(**kw) -> dict:
    info = {
        "title": "固态电池进展速览",
        "uploader": "虚构科技频道",
        "upload_date": "20261101",
        "duration": 118,
        "webpage_url": "https://www.bilibili.com/video/BV1test",
        "subtitles": {},
        "automatic_captions": {},
    }
    info.update(kw)
    return info


# ------------------------------------------------------------------ 时间解析与格式化


class TestTime:
    @pytest.mark.parametrize(
        "spec,expected",
        [("00:10:00", 600), ("10:00", 600), ("600", 600), ("01:02:03", 3723), ("90.5", 90.5)],
    )
    def test_parse_valid(self, spec, expected):
        assert parse_time_spec(spec) == expected

    @pytest.mark.parametrize("spec", ["", "abc", "1:2:3:4", "10:xx", "-5"])
    def test_parse_invalid(self, spec):
        with pytest.raises(ValueError):
            parse_time_spec(spec)

    def test_format_timestamp(self):
        assert format_timestamp(84) == "01:24"
        assert format_timestamp(3723) == "1:02:03"
        assert format_timestamp(-3) == "00:00"

    def test_format_transcript_dedups_consecutive(self):
        segs = [Segment(0, 2, "你好"), Segment(2, 4, "你好"), Segment(4, 6, "世界"), Segment(6, 8, "  ")]
        assert format_transcript(segs) == "[00:00] 你好\n[00:04] 世界"


# ------------------------------------------------------------------ 字幕解析


class TestSubtitleParsing:
    def test_vtt(self):
        segs = parse_vtt(load("sample.vtt"))
        assert len(segs) == 10
        assert segs[0].start == 0 and segs[0].end == 12
        assert "固态电池" in segs[0].text
        assert "<t>" not in segs[0].text  # 标签被去掉
        assert segs[-1].end == 118

    def test_vtt_rolling_duplicates_removed(self):
        text = (
            "WEBVTT\n\n"
            "00:00.000 --> 00:02.000\n你好\n\n"
            "00:02.000 --> 00:04.000\n你好\n\n"
            "00:04.000 --> 00:06.000\n你好世界\n"
        )
        segs = parse_vtt(text)
        assert [s.text for s in segs] == ["你好", "你好世界"]

    def test_json3(self):
        segs = parse_json3(json.loads(load("sample.json3")))
        assert len(segs) == 10  # 没有 segs 的事件被跳过
        assert segs[1].text == "先说结论：能量密度确实提升了，但量产成本还没有降下来。"
        assert segs[1].start == 12.0

    def test_srv3(self):
        segs = parse_srv3(load("sample.srv3"))
        assert len(segs) == 10
        assert segs[2].start == 24.0 and segs[2].end == 36.0

    def test_bili_json(self):
        segs = parse_bili_json(json.loads(load("sample_bili.json")))
        assert len(segs) == 10
        assert segs[0].text.startswith("大家好")

    def test_parse_subtitle_dispatch_and_bad_input(self):
        assert len(parse_subtitle(load("sample.vtt"), "vtt")) == 10
        assert parse_subtitle("不是 JSON", "json3") == []
        assert parse_subtitle("<xml>", "srv3") == []
        assert parse_subtitle(load("sample.vtt"), "ttml") == []


# ------------------------------------------------------------------ 字幕轨选择


class TestTrackSelection:
    def test_manual_before_auto(self):
        info = make_info(
            subtitles={"en": [{"ext": "vtt", "url": "https://s/manual"}]},
            automatic_captions={"zh-Hans": [{"ext": "json3", "url": "https://s/auto"}]},
        )
        track = pick_subtitle_track(info)
        assert track["url"] == "https://s/manual" and track["auto"] == ""

    def test_chinese_before_english(self):
        info = make_info(
            subtitles={
                "en": [{"ext": "vtt", "url": "https://s/en"}],
                "zh-Hans": [{"ext": "json3", "url": "https://s/zh"}],
            }
        )
        assert pick_subtitle_track(info)["lang"] == "zh-Hans"

    def test_danmaku_excluded(self):
        info = make_info(subtitles={"danmaku": [{"ext": "json", "url": "https://s/dm"}]})
        assert pick_subtitle_track(info) is None

    def test_unparsable_ext_skipped(self):
        info = make_info(
            subtitles={"zh": [{"ext": "ttml", "url": "https://s/ttml"}, {"ext": "vtt", "url": "https://s/vtt"}]}
        )
        assert pick_subtitle_track(info)["ext"] == "vtt"

    def test_no_subtitles(self):
        assert pick_subtitle_track(make_info()) is None


# ------------------------------------------------------------------ 字幕质量检查


class TestSubtitleQuality:
    def test_good(self):
        ok, reason = check_subtitle_quality(parse_vtt(load("sample.vtt")), 118)
        assert ok and reason == ""

    def test_empty(self):
        ok, reason = check_subtitle_quality([], 118)
        assert not ok

    def test_too_short_for_duration(self):
        segs = [Segment(0, 10, "只有十几个字的字幕")]
        ok, reason = check_subtitle_quality(segs, 3600)
        assert not ok and "过短" in reason

    def test_coverage_too_low(self):
        segs = [Segment(i * 5, i * 5 + 5, "这是一段足够长的字幕内容，用来凑字数。" * 30) for i in range(6)]
        ok, reason = check_subtitle_quality(segs, 3600)  # 字数够，但只覆盖到 30 秒
        assert not ok and "覆盖" in reason

    def test_massive_repetition(self):
        segs = [Segment(i * 4, i * 4 + 4, "重复的一句话") for i in range(30)]
        ok, reason = check_subtitle_quality(segs, 120)
        assert not ok and "重复" in reason

    def test_garbled(self):
        segs = [Segment(0, 100, "\ufffd" * 80 + "正常文字" * 10)]
        ok, reason = check_subtitle_quality(segs, 100)
        assert not ok and "乱码" in reason

    def test_short_video_tolerates_late_gap(self):
        # 短视频（≤60 秒）不检查覆盖率
        segs = [Segment(0, 20, "这是一段足够长的字幕内容，用来凑字数。" * 3)]
        ok, _ = check_subtitle_quality(segs, 40)
        assert ok


# ------------------------------------------------------------------ 时长闸门


class TestGate:
    def test_short_video_proceeds(self):
        action, _ = gate_decision(600, allow_long=False, start_sec=None, end_sec=None, provider="openai_compatible")
        assert action == "proceed"

    def test_long_video_needs_confirmation(self):
        action, msg = gate_decision(2700, allow_long=False, start_sec=None, end_sec=None, provider="openai_compatible")
        assert action == "confirm" and "--allow-long" in msg

    def test_allow_long_overrides_gate(self):
        action, _ = gate_decision(2700, allow_long=True, start_sec=None, end_sec=None, provider="local_whisper")
        assert action == "proceed"

    def test_over_two_hours_refused_by_default(self):
        action, msg = gate_decision(9000, allow_long=True, start_sec=None, end_sec=None, provider="openai_compatible")
        assert action == "refuse" and "时间段" in msg

    def test_range_shortens_effective_duration(self):
        action, _ = gate_decision(9000, allow_long=False, start_sec=600, end_sec=1200, provider="openai_compatible")
        assert action == "proceed"

    def test_range_over_gate_still_confirms(self):
        action, _ = gate_decision(9000, allow_long=False, start_sec=0, end_sec=3600, provider="openai_compatible")
        assert action == "confirm"


# ------------------------------------------------------------------ 抽帧挑选


class TestFrameSelection:
    def test_min_gap(self):
        pairs = [(Path(f"s{i}.jpg"), float(i)) for i in range(10)]  # 每秒一帧
        kept = video._select_frames(pairs, 10)
        times = [t for _, t in kept]
        assert times == [0.0, 3.0, 6.0, 9.0]

    def test_cap_and_keep_ends(self):
        pairs = [(Path(f"s{i}.jpg"), float(i * 4)) for i in range(100)]
        kept = video._select_frames(pairs, 400)
        assert len(kept) == video.FRAME_MAX_COUNT
        assert kept[0][1] == 0.0 and kept[-1][1] == 396.0


# ------------------------------------------------------------------ 漏斗流程（打桩 yt-dlp 与网络）


class FakeYDL:
    """假 yt-dlp：记录参数；download 时按 outtmpl 造一个假文件。"""

    instances: list["FakeYDL"] = []
    info: dict = {}
    fail_download: bool = False

    def __init__(self, opts):
        self.opts = opts
        self.downloaded: list[str] = []
        FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        assert download is False, "L0 不得下载媒体"
        return dict(FakeYDL.info)

    def download(self, urls):
        if FakeYDL.fail_download:
            raise RuntimeError("simulated download failure")
        target = Path(self.opts["outtmpl"].replace("%(ext)s", "m4a"))
        target.write_bytes(b"fake media")
        self.downloaded.extend(urls)


@pytest.fixture
def fake_ytdlp(monkeypatch):
    FakeYDL.instances = []
    FakeYDL.info = make_info()
    FakeYDL.fail_download = False
    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeYDL
    module.utils = types.SimpleNamespace(download_range_func=lambda chapters, ranges: ("ranges", ranges))
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    return module


@pytest.fixture
def no_asr(monkeypatch):
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.delenv("ASR_API_KEY", raising=False)


@pytest.fixture
def cache_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "cache_root", lambda: tmp_path / "cache")
    return tmp_path / "cache"


def run_extract(url="https://www.bilibili.com/video/BV1test", **kw):
    return VideoExtractor("bilibili").extract(url, time.monotonic() + 10, **kw)


class TestFunnel:
    def test_l0_never_downloads_media(self, fake_ytdlp, no_asr, cache_tmp, monkeypatch):
        monkeypatch.setattr(video, "http_get", lambda *a, **k: Fetched(text=load("sample.vtt")))
        FakeYDL.info = make_info(subtitles={"zh-Hans": [{"ext": "vtt", "url": "https://s/vtt"}]})
        r = run_extract()
        assert_contract(r)
        assert all(not i.downloaded for i in FakeYDL.instances)
        assert all(i.opts.get("skip_download") for i in FakeYDL.instances)

    def test_l1_success_stops_at_l1(self, fake_ytdlp, no_asr, cache_tmp, monkeypatch):
        monkeypatch.setattr(video, "http_get", lambda *a, **k: Fetched(text=load("sample.vtt")))
        FakeYDL.info = make_info(subtitles={"zh-Hans": [{"ext": "vtt", "url": "https://s/vtt"}]})
        r = run_extract()
        assert_contract(r)
        assert r["status"] == "success"
        assert r["funnel_level"] == "L1"
        assert r["extraction_method"] == "subtitle"
        assert r["source_type"] == "video" and r["platform"] == "bilibili"
        assert r["duration_sec"] == 118
        assert r["publish_date"] == "2026-11-01"
        assert "[00:00]" in r["content_text"] and "固态电池" in r["content_text"]
        # 逐字稿已缓存，供 --frames 复用
        assert (cache_dir_for(r["url"]) / "transcript.json").exists()

    def test_bad_subtitles_without_asr_returns_partial_guidance(self, fake_ytdlp, no_asr, cache_tmp, monkeypatch):
        monkeypatch.setattr(video, "http_get", lambda *a, **k: Fetched(text="WEBVTT\n\n00:00.000 --> 00:02.000\n短\n"))
        FakeYDL.info = make_info(subtitles={"zh-Hans": [{"ext": "vtt", "url": "https://s/vtt"}]})
        r = run_extract()
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["error_reason"] == "asr_not_configured"
        assert r["funnel_level"] == "L0"
        assert r["content_text"] == ""  # 不拿不合格字幕冒充内容
        assert "ASR" in r["user_hint"] and "文字稿" in r["user_hint"]

    def test_no_subtitles_without_asr_returns_partial(self, fake_ytdlp, no_asr, cache_tmp):
        r = run_extract()
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["error_reason"] == "asr_not_configured"

    def test_long_video_gate_before_download(self, fake_ytdlp, cache_tmp, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "test-key")
        FakeYDL.info = make_info(duration=2700)
        r = run_extract()
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["needs_confirmation"] and "--allow-long" in r["needs_confirmation"]
        assert all(not i.downloaded for i in FakeYDL.instances)  # 未同意前不下载

    def test_over_two_hours_refused(self, fake_ytdlp, cache_tmp, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        FakeYDL.info = make_info(duration=9000)
        r = run_extract()
        assert r["status"] == "partial"
        assert "时间段" in r["needs_confirmation"]
        assert all(not i.downloaded for i in FakeYDL.instances)

    def test_l2_downloads_audio_only_and_transcribes(self, fake_ytdlp, cache_tmp, monkeypatch):
        import asr

        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "test-key")
        monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/" + name)

        def fake_run(cmd, timeout):
            if cmd[0] == "ffmpeg":
                Path(cmd[-1]).write_bytes(b"fake wav")
            return True, ""

        monkeypatch.setattr(video, "_run", fake_run)
        monkeypatch.setattr(
            asr, "transcribe_file",
            lambda wav, offset_sec=0.0: {
                "ok": True, "partial": False,
                "segments": [(offset_sec + 0.0, offset_sec + 5.0, "转写的第一句话"), (offset_sec + 5.0, offset_sec + 10.0, "转写的第二句话")],
            },
        )
        r = run_extract()
        assert_contract(r)
        assert r["status"] == "success"
        assert r["funnel_level"] == "L2" and r["extraction_method"] == "asr"
        assert "[00:00] 转写的第一句话" in r["content_text"]
        # 只下载了音频流，没有视频格式
        download_opts = [i.opts for i in FakeYDL.instances if i.downloaded]
        assert len(download_opts) == 1
        assert "audio" in download_opts[0]["format"] and "video" not in download_opts[0]["format"]

    def test_l2_range_passes_download_sections_and_offset(self, fake_ytdlp, cache_tmp, monkeypatch):
        import asr

        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "test-key")
        monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(video, "_run", lambda cmd, timeout: (Path(cmd[-1]).write_bytes(b"x"), (True, ""))[1])
        seen = {}

        def fake_transcribe(wav, offset_sec=0.0):
            seen["offset"] = offset_sec
            return {"ok": True, "partial": False, "segments": [(offset_sec, offset_sec + 5, "段落内容")]}

        monkeypatch.setattr(asr, "transcribe_file", fake_transcribe)
        FakeYDL.info = make_info(duration=9000)
        r = run_extract(start_sec=600.0, end_sec=1200.0)
        assert r["status"] == "success"
        assert seen["offset"] == 600.0
        assert "[10:00] 段落内容" in r["content_text"]
        download_opts = [i.opts for i in FakeYDL.instances if i.downloaded][0]
        assert download_opts["download_ranges"] == ("ranges", [(600.0, 1200.0)])

    def test_l2_partial_transcript_is_not_success(self, fake_ytdlp, cache_tmp, monkeypatch):
        import asr

        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "test-key")
        monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(video, "_run", lambda cmd, timeout: (Path(cmd[-1]).write_bytes(b"x"), (True, ""))[1])
        monkeypatch.setattr(
            asr, "transcribe_file",
            lambda wav, offset_sec=0.0: {"ok": True, "partial": True, "segments": [(0, 5, "只有前半段")]},
        )
        r = run_extract()
        assert r["status"] == "partial"
        assert "只有前半段" in r["content_text"]
        assert "一部分" in r["user_hint"]

    def test_l0_login_wall_is_failed_without_content(self, fake_ytdlp, no_asr, cache_tmp):
        FakeYDL.info = make_info(availability="premium_only")
        r = run_extract()
        assert_contract(r)
        assert r["status"] == "failed"
        assert r["error_reason"] == "login_wall"
        assert r["content_text"] == ""

    def test_frames_reuses_cached_transcript_without_refetch(self, fake_ytdlp, no_asr, cache_tmp, monkeypatch):
        url = "https://www.bilibili.com/video/BV1test"
        extractor = VideoExtractor("bilibili")
        cache_dir = cache_dir_for(url)
        cache_dir.mkdir(parents=True)
        FakeYDL.info = make_info(subtitles={"zh": [{"ext": "vtt", "url": "https://s/vtt"}]})
        monkeypatch.setattr(video, "http_get", lambda *a, **k: Fetched(text=load("sample.vtt")))
        first = run_extract(url)

        def should_not_call(*a, **k):
            raise AssertionError("有缓存时不应重新走漏斗")

        monkeypatch.setattr(VideoExtractor, "_l0_metadata", should_not_call)
        called = {}

        def fake_l3(self, url_, cache_dir_, cached, start, end):
            called["transcript"] = cached.get("content_text")
            return {"marker": True}

        monkeypatch.setattr(VideoExtractor, "_l3_frames", fake_l3)
        r = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert r == {"marker": True}
        assert called["transcript"] == first["content_text"]

    def test_cache_key_includes_range(self):
        a = cache_dir_for("https://x/v", None, None)
        b = cache_dir_for("https://x/v", 600, 1200)
        assert a != b


@pytest.fixture
def recorded_asr(fake_ytdlp, cache_tmp, monkeypatch):
    """可计数的云端 ASR 替身；保留真实的缓存读写和 L3 返回路径。"""
    import asr

    offsets = []
    monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
    monkeypatch.setenv("ASR_API_KEY", "mock-only")
    monkeypatch.setattr(video.shutil, "which", lambda name: name)

    def fake_run(cmd, timeout):
        target = Path(cmd[-1])
        if target.suffix == ".wav":
            target.write_bytes(b"audio")
            return True, ""
        assert target.name == "scene_%04d.jpg"
        target.with_name("scene_0001.jpg").write_bytes(JPEG_FRAME)
        return True, "[Parsed_showinfo] pts_time:2"

    monkeypatch.setattr(video, "_run", fake_run)

    def configure(partial):
        def transcribe(wav, offset_sec=0.0):
            offsets.append(offset_sec)
            return {
                "ok": True, "partial": partial,
                "segments": [(offset_sec, offset_sec + 4, f"第 {len(offsets)} 份稿")],
                "error_reason": "anti_scrape" if partial else "",
                "user_hint": "只转写了前一部分，后续切片被限流。" if partial else "",
            }
        monkeypatch.setattr(asr, "transcribe_file", transcribe)
        return offsets

    return configure


class TestTranscriptReuse:
    @pytest.mark.parametrize("partial", [True, False], ids=["partial", "complete"])
    def test_plain_reuses_asr_and_metadata(self, recorded_asr, partial):
        offsets = recorded_asr(partial)
        r1 = run_extract(start_sec=10.0, end_sec=40.0)
        r2 = run_extract(start_sec=10.0, end_sec=40.0)
        assert offsets == [10.0]
        assert sum(bool(i.opts.get("skip_download")) for i in FakeYDL.instances) == 1
        assert_contract(r2)
        assert r2 == r1
        assert r2["status"] == ("partial" if partial else "success")
        assert r2["error_reason"] == ("anti_scrape" if partial else "")
        assert r2["extraction_method"] == "asr" and r2["funnel_level"] == "L2"
        assert r2["content_text"] == "[00:10] 第 1 份稿"

    @pytest.mark.parametrize("first_frames", [True, False], ids=["first-frames", "followup-frames"])
    @pytest.mark.parametrize("partial", [True, False], ids=["partial", "complete"])
    def test_frames_never_upgrade_transcript(self, recorded_asr, first_frames, partial):
        offsets = recorded_asr(partial)
        FakeYDL.info = make_info(webpage_url="https://www.bilibili.com/video/BVcanonical")
        segment = dict(start_sec=10.0, end_sec=40.0)
        first = run_extract(frames=first_frames, **segment)
        framed = first if first_frames else run_extract(frames=True, **segment)
        again = run_extract(frames=True, **segment)
        plain = run_extract(**segment)
        for result in (framed, again):
            assert_contract(result)
            assert result["status"] == ("partial" if partial else "success")
            assert result["error_reason"] == ("anti_scrape" if partial else "")
            assert ("后续切片被限流" in result["user_hint"]) == partial
            assert result["extraction_method"] == "asr+frames" and result["funnel_level"] == "L3"
            assert [Path(p).name for p in result["keyframes"]] == ["frame_00012s.jpg"]
            assert result["content_text"] == plain["content_text"] == "[00:10] 第 1 份稿"
            assert result["url"] == plain["url"] == FakeYDL.info["webpage_url"]
            assert result["title"] == plain["title"] == FakeYDL.info["title"]
        assert plain["status"] == framed["status"] and plain["keyframes"] == []
        assert plain["extraction_method"] == "asr" and plain["funnel_level"] == "L2"
        assert offsets == [10.0]
        assert sum(bool(i.opts.get("skip_download")) for i in FakeYDL.instances) == 1
        assert sum(bool(i.downloaded) for i in FakeYDL.instances) == 2

    def test_frame_failure_keeps_both_limitations(self, recorded_asr, monkeypatch):
        recorded_asr(True)
        transcript = run_extract()
        monkeypatch.setattr(video.shutil, "which", lambda name: None)
        result = run_extract(frames=True)
        assert result["status"] == "partial" and result["error_reason"] == "anti_scrape"
        assert "后续切片被限流" in result["user_hint"] and "ffmpeg" in result["user_hint"]
        assert result["keyframes"] == [] and result["extraction_method"] == "asr"
        assert run_extract() == transcript

    def test_different_ranges_and_urls_do_not_mix(self, recorded_asr):
        offsets = recorded_asr(False)
        requests = [("A", 0, 20), ("A", 20, 40), ("B", 0, 20), ("A", 0, 30)]
        first = [run_extract(f"https://example.test/{name}", start_sec=a, end_sec=b) for name, a, b in requests]
        assert len({r["content_text"] for r in first}) == 4
        for (name, a, b), expected in zip(requests, first):
            assert run_extract(f"https://example.test/{name}", start_sec=float(a), end_sec=float(b)) == expected
        assert offsets == [0, 20, 0, 0]

    @pytest.mark.parametrize("payload", [b"{", b"\xff", b"[]", b'{"content_text":"unverified","extraction_method":"asr"}'])
    def test_unknown_or_corrupt_transcript_is_not_evidence(self, cache_tmp, fake_ytdlp, no_asr, monkeypatch, payload):
        url = "https://www.bilibili.com/video/BVcache"
        directory = cache_dir_for(url)
        directory.mkdir(parents=True)
        (directory / "transcript.json").write_bytes(payload)
        monkeypatch.setattr(video.shutil, "which", lambda name: None)
        r = run_extract(url, frames=True)
        assert_contract(r)
        assert r["status"] == "partial" and r["error_reason"] == "asr_not_configured"
        assert r["keyframes"] == [] and r["content_text"] == ""
        assert "截图" in r["user_hint"]

    @pytest.mark.parametrize("damage", ["request", "bool_offset", "duration", "status", "method", "text", "missing_reason", "confirmation"])
    def test_invalid_current_record_does_not_replay(self, recorded_asr, monkeypatch, damage):
        url = "https://www.bilibili.com/video/BVcache"
        recorded_asr(True)
        run_extract(url, start_sec=0)
        path = cache_dir_for(url, 0) / "transcript.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        if damage in ("request", "bool_offset"):
            record["request"][1] = False if damage == "bool_offset" else 60
        else:
            key, value = {"duration": ("duration_sec", "oops"), "status": ("status", "success"),
                          "method": ("extraction_method", "subtitle"), "text": ("content_text", []),
                          "confirmation": ("needs_confirmation", []), "missing_reason": ("error_reason", "")}[damage]
            record["result"][key] = value
        path.write_text(json.dumps(record), encoding="utf-8")
        monkeypatch.delenv("ASR_PROVIDER", raising=False)
        r = run_extract(url, start_sec=0)
        assert_contract(r)
        assert r["status"] == "partial" and r["content_text"] == ""
        assert r["error_reason"] == "asr_not_configured"

    @pytest.mark.parametrize("damage", ["48h", "changed_digit", "unsigned"])
    def test_expired_or_unverified_text_is_reacquired(self, recorded_asr, damage):
        url = "https://example.test/cached"
        offsets = recorded_asr(False)
        first = run_extract(url)
        path = cache_dir_for(url) / "transcript.json"
        if damage == "48h":
            old = time.time() - 48 * 3600
            os.utime(path, (old, old))
        else:
            record = json.loads(path.read_text(encoding="utf-8"))
            if damage == "changed_digit":
                record["result"]["content_text"] = first["content_text"].replace("1", "9")
            else:
                record = {k: record[k] for k in ("request", "result")}
            path.write_text(json.dumps(record), encoding="utf-8")
        second = run_extract(url)
        assert offsets == [0, 0] and second["content_text"] == "[00:00] 第 2 份稿"
        assert run_extract(url) == second and offsets == [0, 0]

    def test_read_does_not_renew_transcript_lifetime(self, recorded_asr, monkeypatch):
        url = "https://example.test/lifetime"
        offsets = recorded_asr(False)
        first = run_extract(url)
        path = cache_dir_for(url) / "transcript.json"
        written = path.stat().st_mtime
        monkeypatch.setattr(video.time, "time", lambda: written + 24 * 3600 - 1)
        assert run_extract(url) == first and offsets == [0]
        monkeypatch.setattr(video.time, "time", lambda: written + 24 * 3600 + 1)
        assert run_extract(url)["content_text"] == "[00:00] 第 2 份稿"

    def test_cache_write_failure_preserves_result_and_old_record(self, recorded_asr, monkeypatch):
        url = "https://example.test/write-failure"
        recorded_asr(False)
        first = run_extract(url)
        directory = cache_dir_for(url)
        path = directory / "transcript.json"
        old = time.time() - 48 * 3600
        os.utime(path, (old, old))
        previous = path.read_bytes()
        entries = set(directory.iterdir())

        def fail_replace(src, dst):
            assert Path(dst) == path and Path(src).read_bytes() != previous
            assert path.read_bytes() == previous  # 尚未提交时，读者仍只能看到完整旧档。
            raise PermissionError("locked")

        monkeypatch.setattr(os, "replace", fail_replace)
        second = run_extract(url)
        assert second["status"] == "success" and second["content_text"] != first["content_text"]
        assert path.read_bytes() == previous and set(directory.iterdir()) == entries
        assert VideoExtractor("bilibili")._load_transcript(directory, url, None, None) is None

    @pytest.mark.parametrize("payload", [b"not an image", JPEG_FRAME[:-2], JPEG_FRAME[:-3] + b"\x00\xff\xd9"])
    def test_corrupt_frame_is_not_replayed_as_a_screenshot(self, recorded_asr, monkeypatch, payload):
        offsets = recorded_asr(False)
        original = run_extract(frames=True)
        path, = map(Path, original["keyframes"])
        path.write_bytes(payload)
        monkeypatch.setattr(video.shutil, "which", lambda name: None)
        framed = run_extract(frames=True)
        assert framed["status"] == "partial" and framed["keyframes"] == []
        assert framed["extraction_method"] == "asr" and framed["content_text"] == original["content_text"]
        assert run_extract()["status"] == "success" and offsets == [0.0]

    def test_no_speech_returns_screenshot_guidance_without_frames(self, recorded_asr, monkeypatch):
        import asr

        recorded_asr(False)
        monkeypatch.setattr(asr, "transcribe_file", lambda *a, **k: {"ok": True, "partial": False, "segments": []})
        result = run_extract(frames=True)
        assert_contract(result)
        assert result["status"] == "partial" and result["keyframes"] == [] and result["content_text"] == ""
        assert "截图" in result["user_hint"] and result["extraction_method"] == ""
        assert not (cache_dir_for(result["url"]) / "transcript.json").exists()

    def test_subtitle_range_is_not_replaced_by_whole_video(self, fake_ytdlp, cache_tmp, no_asr, monkeypatch):
        FakeYDL.info = make_info(subtitles={"zh": [{"ext": "vtt", "url": "https://s/vtt"}]})
        monkeypatch.setattr(video, "http_get", lambda *a, **k: Fetched(text=load("sample.vtt")))
        first = run_extract(start_sec=12, end_sec=24)
        assert first["content_text"] == "[00:12] " + parse_vtt(load("sample.vtt"))[1].text
        assert first["status"] == "success" and first["extraction_method"] == "subtitle"
        assert run_extract(start_sec=12, end_sec=24) == first
        assert len(FakeYDL.instances) == 1


# ------------------------------------------------------------------ L3 抽帧（打桩 ffmpeg）


class TestL3:
    def _prepare(self, monkeypatch, cache_tmp, scene_count=20):
        url = "https://www.bilibili.com/video/BV1test"
        extractor = VideoExtractor("bilibili")
        cache_dir = cache_dir_for(url)
        cache_dir.mkdir(parents=True)
        transcript = video.make_result(
            "success", platform="bilibili", source_type="video", title="t", author="a", duration_sec=118,
            url=url, content_text="[00:00] 逐字稿", extraction_method="asr", funnel_level="L2",
        )
        extractor._save_transcript(cache_dir, url, None, None, transcript)
        monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/" + name)

        def fake_run(cmd, timeout):
            if "select='gt(scene" in " ".join(cmd):
                out_dir = Path(cmd[-1]).parent
                out_dir.mkdir(parents=True, exist_ok=True)
                stderr = []
                for i in range(scene_count):
                    (out_dir / f"scene_{i + 1:04d}.jpg").write_bytes(JPEG_FRAME)
                    stderr.append(f"[Parsed_showinfo] pts_time:{i * 1.0}")
                return True, "\n".join(stderr)
            return True, ""

        monkeypatch.setattr(video, "_run", fake_run)
        return extractor, url, cache_dir

    def test_frames_capped_and_renamed(self, fake_ytdlp, cache_tmp, monkeypatch):
        extractor, url, cache_dir = self._prepare(monkeypatch, cache_tmp, scene_count=60)
        r = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert_contract(r)
        assert r["status"] == "success"
        assert r["funnel_level"] == "L3"
        assert r["extraction_method"] == "asr+frames"
        assert 0 < len(r["keyframes"]) <= video.FRAME_MAX_COUNT
        assert all(Path(p).exists() for p in r["keyframes"])
        assert all(Path(p).name.startswith("frame_") for p in r["keyframes"])
        # 未选中的场景帧已删除
        assert not list((cache_dir / "frames").glob("scene_*.jpg"))
        # 视频下载用了 ≤480p 的格式
        download_opts = [i.opts for i in FakeYDL.instances if i.downloaded][0]
        assert "height<=480" in download_opts["format"]

    def test_frames_second_call_reuses_cache(self, fake_ytdlp, cache_tmp, monkeypatch):
        extractor, url, cache_dir = self._prepare(monkeypatch, cache_tmp)
        r1 = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert r1["status"] == "success"
        downloads_before = sum(len(i.downloaded) for i in FakeYDL.instances)
        r2 = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert r2["keyframes"] == r1["keyframes"]
        assert sum(len(i.downloaded) for i in FakeYDL.instances) == downloads_before  # 没有重复下载

    def test_frames_without_transcript_is_honest_failure(self, fake_ytdlp, cache_tmp, monkeypatch):
        # 直接调用 L3：没有逐字稿时不冒充看过画面，诚实返回失败
        monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/" + name)
        url = "https://www.bilibili.com/video/BVempty"
        r = VideoExtractor("bilibili")._l3_frames(url, cache_dir_for(url), {}, None, None)
        assert_contract(r)
        assert r["status"] == "failed"
        assert r["keyframes"] == []

    @pytest.mark.parametrize("hard", ["failed_with_output", "no_output", "missing_time", "count_mismatch"])
    def test_unproven_frames_never_become_zero_second_evidence(self, fake_ytdlp, cache_tmp, monkeypatch, hard):
        extractor, url, cache_dir = self._prepare(monkeypatch, cache_tmp)
        frames = cache_dir / "frames"
        frames.mkdir()
        leftovers = [frames / n for n in ("scene_0001.jpg", "fallback_00.jpg")]
        for path in leftovers:
            path.write_bytes(JPEG_FRAME)
        monkeypatch.setattr(video, "_scan_scene_scores", lambda _: [(4.0, 0.05)])

        def fake_run(cmd, timeout):
            if Path(cmd[-1]).name == "scene_%04d.jpg":
                if hard != "no_output":
                    Path(cmd[-1]).with_name("scene_0001.jpg").write_bytes(JPEG_FRAME)
                return hard != "failed_with_output", {
                    "failed_with_output": "[Parsed_showinfo] pts_time:4",
                    "no_output": "", "missing_time": "[Parsed_showinfo] pts_time:N/A",
                    "count_mismatch": "pts_time:2\npts_time:4",
                }[hard]
            return True, ""  # 软切和均匀兜底均未实际产出文件。

        monkeypatch.setattr(video, "_run", fake_run)
        r = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert r["status"] == "partial" and r["keyframes"] == []
        assert r["extraction_method"] == "asr" and r["content_text"] == "[00:00] 逐字稿"
        assert set(frames.iterdir()) == set(leftovers)

    def test_unknown_hard_timestamp_uses_only_fresh_seek_output(self, fake_ytdlp, cache_tmp, monkeypatch):
        extractor, url, _ = self._prepare(monkeypatch, cache_tmp)
        monkeypatch.setattr(video, "_scan_scene_scores", lambda _: [])

        def fake_run(cmd, timeout):
            if "-ss" not in cmd:
                Path(cmd[-1]).with_name("scene_0001.jpg").write_bytes(JPEG_FRAME)
            else:
                assert cmd[cmd.index("-ss") + 1] == "59.00"
                Path(cmd[-1]).write_bytes(JPEG_FRAME)
            return True, ""

        monkeypatch.setattr(video, "_run", fake_run)
        r = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert r["status"] == "success" and [Path(p).name for p in r["keyframes"]] == ["frame_00059s.jpg"]

    def test_static_video_falls_back_to_even_frames(self, fake_ytdlp, cache_tmp, monkeypatch):
        # 单镜头视频场景检测抽不到帧：不算失败，按均匀间隔兜底
        extractor, url, cache_dir = self._prepare(monkeypatch, cache_tmp)
        # 持续运动（手持拍摄）：软切换判定应全部拒绝，仍走兜底
        monkeypatch.setattr(video, "_scan_scene_scores", lambda p, timeout=600: [(i * 0.1, 0.05) for i in range(1180)])

        def fake_run(cmd, timeout):
            joined = " ".join(cmd)
            if "select='gt(scene" in joined:
                return False, "Nothing was written into output file"
            if "-ss" in cmd:  # 兜底抽帧
                Path(cmd[-1]).write_bytes(JPEG_FRAME)
            return True, ""

        monkeypatch.setattr(video, "_run", fake_run)
        r = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert_contract(r)
        assert r["status"] == "success"
        assert r["funnel_level"] == "L3"
        assert 0 < len(r["keyframes"]) <= 8
        assert all(Path(p).exists() for p in r["keyframes"])

    def test_soft_slide_transitions_extracted(self, fake_ytdlp, cache_tmp, monkeypatch):
        # 幻灯片录屏：硬切 0 帧，但换页是“低分变化后静止”：应按软切换抽到换页帧
        extractor, url, cache_dir = self._prepare(monkeypatch, cache_tmp)
        slide_scores = [
            (5.8, 0.023), (19.6, 0.036), (24.1, 0.030), (24.6, 0.033),
            (24.8, 0.038), (29.2, 0.022), (57.8, 0.030),
        ]
        monkeypatch.setattr(video, "_scan_scene_scores", lambda p, timeout=600: slide_scores)

        def fake_run(cmd, timeout):
            joined = " ".join(cmd)
            if "select='gt(scene" in joined:
                return True, ""  # 硬切一帧都没有
            if "-ss" in cmd:
                Path(cmd[-1]).write_bytes(JPEG_FRAME)
            return True, ""

        monkeypatch.setattr(video, "_run", fake_run)
        r = extractor.extract(url, time.monotonic() + 10, frames=True)
        assert_contract(r)
        assert r["status"] == "success"
        names = [Path(p).name for p in r["keyframes"]]
        # 取每段过渡末帧：5.8→6s、19.6→20s、24.8→25s、29.2→29s、57.8→58s
        assert names == ["frame_00006s.jpg", "frame_00020s.jpg", "frame_00025s.jpg",
                         "frame_00029s.jpg", "frame_00058s.jpg"]


class TestSoftSceneTimes:
    def test_slide_like_changes_keep_tail_of_each_transition(self):
        scores = [(5.8, 0.023), (19.6, 0.036), (24.1, 0.030), (24.6, 0.033),
                  (24.8, 0.038), (29.2, 0.022)]
        assert video._soft_scene_times(scores, duration=127.0) == [5.8, 19.6, 24.8, 29.2]

    def test_continuous_motion_all_rejected(self):
        scores = [(i * 0.1, 0.05) for i in range(200)]
        assert video._soft_scene_times(scores, duration=20.0) == []

    def test_below_soft_threshold_ignored(self):
        assert video._soft_scene_times([(10.0, 0.015), (40.0, 0.019)]) == []

    def test_change_followed_by_motion_rejected(self):
        scores = [(10.0, 0.05), (10.5, 0.02), (11.0, 0.03)]
        assert video._soft_scene_times(scores, duration=30.0) == [11.0]


# ------------------------------------------------------------------ 入口参数


class TestEntryPoint:
    def test_invalid_time_spec_outputs_json(self, capsys):
        code = fc.main(["https://www.bilibili.com/video/BV1x", "--start", "abc"])
        assert code == 0
        r = json.loads(capsys.readouterr().out)
        assert_contract(r)
        assert r["status"] == "failed" and "时间格式" in r["user_hint"]

    def test_end_before_start_outputs_json(self, capsys):
        code = fc.main(["https://www.bilibili.com/video/BV1x", "--start", "00:20:00", "--end", "00:10:00"])
        assert code == 0
        r = json.loads(capsys.readouterr().out)
        assert r["status"] == "failed" and "结束时间" in r["user_hint"]


class TestLocalMedia:
    def test_local_audio_without_asr_returns_partial_guidance(self, no_asr, cache_tmp, tmp_path, monkeypatch):
        import asr as asr_mod

        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"fake-m4a")
        monkeypatch.setattr(asr_mod, "probe_duration", lambda p: 180.0)
        r = fc.fetch(str(audio))
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["platform"] == "local"
        assert r["title"] == "meeting"
        assert r["error_reason"] == "asr_not_configured"
        assert r["duration_sec"] == 180.0

    def test_local_long_media_triggers_gate_before_ffmpeg(self, cache_tmp, tmp_path, monkeypatch):
        import asr as asr_mod

        audio = tmp_path / "long_talk.mp3"
        audio.write_bytes(b"fake-mp3")
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
        monkeypatch.setattr(asr_mod, "probe_duration", lambda p: 2400.0)
        called = []
        monkeypatch.setattr(video, "_run", lambda cmd, timeout: (called.append(cmd), (True, ""))[1])

        r = fc.fetch(str(audio))
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["needs_confirmation"]
        assert not called

    def test_local_audio_transcribes_and_reuses_cache(self, cache_tmp, tmp_path, monkeypatch):
        import asr as asr_mod

        audio = tmp_path / "standup.mp3"
        audio.write_bytes(b"fake-mp3")
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
        monkeypatch.setattr(asr_mod, "probe_duration", lambda p: 120.0)

        transcribe_calls = 0

        def fake_run(cmd, timeout):
            Path(cmd[-1]).write_bytes(b"RIFFfakewav")
            return True, ""

        def fake_transcribe(wav, offset_sec=0.0):
            nonlocal transcribe_calls
            transcribe_calls += 1
            return {"ok": True, "partial": False, "segments": [(offset_sec + 1.0, offset_sec + 5.0, "同步本周进度")]}

        monkeypatch.setattr(video, "_run", fake_run)
        monkeypatch.setattr(asr_mod, "transcribe_file", fake_transcribe)

        r1 = fc.fetch(str(audio))
        assert_contract(r1)
        assert r1["status"] == "success"
        assert r1["platform"] == "local"
        assert r1["funnel_level"] == "L2"
        assert r1["extraction_method"] == "asr"
        assert "同步本周进度" in r1["content_text"]
        assert transcribe_calls == 1

        # 第二次调用应命中缓存，不重复转写；对纯音频传 --frames 时保留逐字稿并说明无画面
        r2 = fc.fetch(str(audio), frames=True)
        assert r2["status"] == "success"
        assert transcribe_calls == 1
        assert "纯音频" in r2["user_hint"]

    def test_local_video_frames_reuses_transcript_and_extracts_frames(self, cache_tmp, tmp_path, monkeypatch):
        import asr as asr_mod

        clip = tmp_path / "slides.mp4"
        clip.write_bytes(b"fake-mp4")
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
        monkeypatch.setattr(asr_mod, "probe_duration", lambda p: 90.0)
        monkeypatch.setattr(asr_mod, "transcribe_file", lambda wav, offset_sec=0.0: {
            "ok": True, "partial": False, "segments": [(0.0, 4.0, "看这张柱状图")]
        })

        def fake_run(cmd, timeout):
            out = Path(cmd[-1])
            if out.suffix == ".wav":
                out.write_bytes(b"RIFFfakewav")
                return True, ""
            if "scene_%04d.jpg" in str(out):
                (out.parent / "scene_0001.jpg").write_bytes(JPEG_FRAME)
                return True, "pts_time:12.0"
            return True, ""

        monkeypatch.setattr(video, "_run", fake_run)
        r = fc.fetch(str(clip), frames=True)
        assert_contract(r)
        assert r["status"] == "success"
        assert r["platform"] == "local"
        assert r["funnel_level"] == "L3"
        assert r["extraction_method"] == "asr+frames"
        assert len(r["keyframes"]) == 1

    def test_local_unknown_duration_triggers_confirm_gate(self, cache_tmp, tmp_path, monkeypatch):
        """缺 ffprobe 导致时长未知时，必须触发确认闸门，不得按 0s 悄悄调付费 ASR（P0-3）。"""
        import asr as asr_mod

        audio = tmp_path / "unknown_len.mp3"
        audio.write_bytes(b"fake-mp3")
        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "sk-test-fake")
        monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
        monkeypatch.setattr(asr_mod, "probe_duration", lambda p: None)
        called = []
        monkeypatch.setattr(video, "_run", lambda cmd, timeout: (called.append(cmd), (True, ""))[1])

        r = fc.fetch(str(audio))
        assert_contract(r)
        assert r["status"] == "partial"
        assert r["needs_confirmation"]
        assert not called

    def test_local_video_range_frames_seek_and_single_offset(self, cache_tmp, tmp_path, monkeypatch):
        """本地视频带 --start/--end 抽帧必须传 -ss/-t 截断区间，且文件名时间戳只加一次 start_sec（P0-2）。"""
        import asr as asr_mod

        clip = tmp_path / "talk.mp4"
        clip.write_bytes(b"fake-mp4")
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
        monkeypatch.setattr(asr_mod, "probe_duration", lambda p: 1800.0)
        monkeypatch.setattr(asr_mod, "transcribe_file", lambda wav, offset_sec=0.0: {
            "ok": True, "partial": False, "segments": [(offset_sec + 5.0, offset_sec + 10.0, "区间内容")]
        })

        seen_cmds: list[list[str]] = []

        def fake_run(cmd, timeout):
            seen_cmds.append(list(cmd))
            out = Path(cmd[-1])
            if out.suffix == ".wav":
                out.write_bytes(b"RIFFfakewav")
                return True, ""
            if "scene_%04d.jpg" in str(out):
                (out.parent / "scene_0001.jpg").write_bytes(JPEG_FRAME)
                return True, "pts_time:50.0"
            return True, ""

        monkeypatch.setattr(video, "_run", fake_run)
        r = fc.fetch(str(clip), frames=True, start_sec=600.0, end_sec=900.0)
        assert_contract(r)
        assert r["status"] == "success"
        # 600 + 50 = 650s，绝不是 1250s
        assert len(r["keyframes"]) == 1
        assert Path(r["keyframes"][0]).name == "frame_00650s.jpg"
        scene_cmd = next(c for c in seen_cmds if "scene_%04d.jpg" in c[-1])
        assert "-ss" in scene_cmd and "600.00" in scene_cmd
        assert "-t" in scene_cmd and "300.00" in scene_cmd
        # 音频切段采用 output seeking（-ss 在 -i 之后）
        wav_cmd = next(c for c in seen_cmds if c[-1].endswith(".wav"))
        assert wav_cmd.index("-i") < wav_cmd.index("-ss")

    def test_missing_local_media_filename_never_treated_as_domain(self, monkeypatch):
        """拼错或不存在的本地音视频文件名不得被当成域名发外网请求（P1-5）。"""
        from extractors import base as base_mod

        def forbid_http(*a, **k):
            raise AssertionError("不应把本地文件名当域名发 HTTP 请求")

        monkeypatch.setattr(base_mod, "http_get", forbid_http)
        for sample in ("meeting.wav", "slides.mov", "C:/nonexistent/talk.mp3"):
            r = fc.fetch(sample)
            assert_contract(r)
            assert r["status"] == "failed"
            assert r["platform"] == "local"
            assert r["error_reason"] == "not_found"

    def test_default_video_extractor_extract_local_hits_cache(self, cache_tmp, tmp_path, monkeypatch):
        """即便以默认 VideoExtractor() 调用 extract_local，二次调用也必须命中缓存（P1-12）。"""
        import asr as asr_mod

        audio = tmp_path / "memo.mp3"
        audio.write_bytes(b"fake-mp3")
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
        monkeypatch.setattr(asr_mod, "probe_duration", lambda p: 60.0)
        calls = 0

        def fake_run(cmd, timeout):
            Path(cmd[-1]).write_bytes(b"RIFFfakewav")
            return True, ""

        def fake_tr(wav, offset_sec=0.0):
            nonlocal calls
            calls += 1
            return {"ok": True, "partial": False, "segments": [(0.0, 3.0, "纪要要点")]}

        monkeypatch.setattr(video, "_run", fake_run)
        monkeypatch.setattr(asr_mod, "transcribe_file", fake_tr)

        ext = VideoExtractor()  # 默认 platform="web"
        r1 = ext.extract_local(audio)
        r2 = ext.extract_local(audio)
        assert r1["status"] == "success" and r2["status"] == "success"
        assert r1["platform"] == "local" and calls == 1