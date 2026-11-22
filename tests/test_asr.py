"""ASR 的离线测试：提供方选择、切片拼接、失败降级。不联网、不调真实接口。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "dyz-plain-talk-decoder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import asr  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("ASR_PROVIDER", "ASR_API_KEY", "ASR_BASE_URL", "ASR_MODEL"):
        monkeypatch.delenv(key, raising=False)


class TestProvider:
    def test_unset_means_not_configured(self):
        provider, problem = asr.get_provider()
        assert provider == "" and problem

    def test_openai_requires_key(self):
        import os

        os.environ["ASR_PROVIDER"] = "openai_compatible"
        try:
            _, problem = asr.get_provider()
            assert "ASR_API_KEY" in problem
        finally:
            del os.environ["ASR_PROVIDER"]

    def test_valid_providers(self, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        assert asr.get_provider() == ("local_whisper", "")
        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "test-key")
        assert asr.get_provider() == ("openai_compatible", "")

    def test_unknown_provider_rejected(self, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "aliyun")
        _, problem = asr.get_provider()
        assert problem


class TestMerge:
    def test_offsets_applied_and_sorted(self):
        parts = [
            [(0.0, 5.0, "第一片开头"), (5.0, 10.0, "第一片结尾")],
            [(0.0, 5.0, "第二片开头")],
        ]
        merged = asr.merge_segments(parts, [0.0, 600.0])
        assert merged == [
            (0.0, 5.0, "第一片开头"),
            (5.0, 10.0, "第一片结尾"),
            (600.0, 605.0, "第二片开头"),
        ]

    def test_object_segments_supported(self):
        class Seg:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        merged = asr.merge_segments([[Seg(1, 2, " 你好 ")]], [60.0])
        assert merged == [(61.0, 62.0, "你好")]

    def test_empty_text_dropped(self):
        assert asr.merge_segments([[(0, 1, "  ")]], [0]) == []


class TestTranscribe:
    def test_not_configured_returns_guidance_not_crash(self, tmp_path):
        r = asr.transcribe_file(tmp_path / "a.wav")
        assert r["ok"] is False
        assert r["error_reason"] == "asr_not_configured"
        assert "文字稿" in r["user_hint"]

    def test_stubbed_slices_joined_with_offset(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        # 打桩切片：两片，偏移 0 与 600
        monkeypatch.setattr(
            asr, "slice_audio",
            lambda audio, work, slice_sec=600: [(tmp_path / "s0.wav", 0.0), (tmp_path / "s1.wav", 600.0)],
        )

        def fake_slice(path):
            if path.name == "s0.wav":
                return [(0.0, 5.0, "开头")]
            return [(0.0, 5.0, "第二段")]

        r = asr.transcribe_file(tmp_path / "a.wav", offset_sec=60.0, transcribe_slice=fake_slice)
        assert r["ok"] is True and r["partial"] is False
        assert r["segments"] == [(60.0, 65.0, "开头"), (660.0, 665.0, "第二段")]

    def test_mid_failure_returns_partial_not_fake_complete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(
            asr, "slice_audio",
            lambda audio, work, slice_sec=600: [(tmp_path / "s0.wav", 0.0), (tmp_path / "s1.wav", 600.0)],
        )

        def fake_slice(path):
            if path.name == "s0.wav":
                return [(0.0, 5.0, "只有前半段")]
            raise ConnectionError("network down")

        r = asr.transcribe_file(tmp_path / "a.wav", transcribe_slice=fake_slice)
        assert r["ok"] is True and r["partial"] is True
        assert r["segments"] == [(0.0, 5.0, "只有前半段")]
        assert "一部分" in r["user_hint"]

    def test_first_slice_failure_is_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(asr, "slice_audio", lambda audio, work, slice_sec=600: [(tmp_path / "s0.wav", 0.0)])

        def fake_slice(path):
            raise ImportError("No module named 'faster_whisper'")

        r = asr.transcribe_file(tmp_path / "a.wav", transcribe_slice=fake_slice)
        assert r["ok"] is False
        assert r["error_reason"] == "missing_dependency"
        assert "faster-whisper" in r["user_hint"]

    def test_api_key_never_in_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "sk-secret-12345")
        monkeypatch.setattr(asr, "slice_audio", lambda audio, work, slice_sec=600: [(tmp_path / "s0.wav", 0.0)])

        def fake_slice(path):
            raise RuntimeError("401 invalid api key sk-secret-12345")

        r = asr.transcribe_file(tmp_path / "a.wav", transcribe_slice=fake_slice)
        assert r["ok"] is False
        assert "sk-secret-12345" not in r["user_hint"]
        assert r["error_reason"] == "login_wall"


class TestSliceAudio:
    @pytest.mark.parametrize("fail_at", [0, 600, 1200, None])
    def test_ffmpeg_failure_never_claims_full_transcript(self, tmp_path, monkeypatch, fail_at):
        from subprocess import CompletedProcess

        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(asr, "probe_duration", lambda p: 1201.0)
        audio = tmp_path / "input.wav"
        audio.write_bytes(b"input")
        calls = []

        def run_ffmpeg(cmd, **kw):
            offset = int(float(cmd[cmd.index("-ss") + 1]))
            if offset == fail_at:
                return CompletedProcess(cmd, 1)
            Path(cmd[-1]).write_bytes(b"slice")
            return CompletedProcess(cmd, 0)

        def transcribe(path):
            calls.append(path.name)
            return [(0.25, 0.75, "前文")]

        monkeypatch.setattr(asr.subprocess, "run", run_ffmpeg)
        r = asr.transcribe_file(audio, offset_sec=30, transcribe_slice=transcribe)
        offsets = [off for off in (0, 600, 1200) if fail_at is None or off < fail_at]
        assert r["ok"] is bool(offsets)
        assert r.get("partial", False) is (bool(offsets) and fail_at is not None)
        assert r["segments"] == [(off + 30.25, off + 30.75, "前文") for off in offsets]
        assert calls == [f"slice_{i:03d}.wav" for i in range(len(offsets))]
        if fail_at is not None:
            assert r["error_reason"] == "network" and "切片" in r["user_hint"]

    @pytest.mark.parametrize("missing", [True, False])
    def test_ffmpeg_missing_distinguished_from_timeout(self, monkeypatch, tmp_path, missing):
        import subprocess

        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(asr, "probe_duration", lambda _: 1201.0)

        def fail(cmd, **kwargs):
            if missing:
                raise FileNotFoundError("ffmpeg")
            raise subprocess.TimeoutExpired(cmd, 120)

        monkeypatch.setattr(asr.subprocess, "run", fail)
        r = asr.transcribe_file(tmp_path / "a.wav", transcribe_slice=lambda _: pytest.fail("没有可转写的片段"))
        assert not r["ok"] and r["segments"] == []
        assert r["error_reason"] == ("missing_dependency" if missing else "network")
        assert ("找不到 ffmpeg" if missing else "超时") in r["user_hint"]

    def test_unknown_large_audio_not_sent_whole(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASR_PROVIDER", "local_whisper")
        monkeypatch.setattr(asr, "probe_duration", lambda p: None)
        audio = tmp_path / "unknown.wav"
        with audio.open("wb") as f:
            f.truncate(30_000_000)
        r = asr.transcribe_file(audio, transcribe_slice=lambda p: pytest.fail("时长未知的大文件不应整份送去转写"))
        assert not r["ok"] and not r["segments"]
        assert r["error_reason"] and "时长" in r["user_hint"]

    def test_pcm_wav_duration_without_ffprobe(self, tmp_path, monkeypatch):
        import wave

        audio = tmp_path / "generated.wav"
        with wave.open(str(audio), "wb") as w:
            w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            w.writeframes(b"\0\0" * 24000)
        monkeypatch.setattr(asr.shutil, "which", lambda _: None)
        assert asr.probe_duration(audio) == 1.5

    def test_short_audio_not_sliced(self, tmp_path, monkeypatch):
        monkeypatch.setattr(asr, "probe_duration", lambda p: 120.0)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        assert asr.slice_audio(audio, tmp_path) == [(audio, 0.0)]

    def test_unknown_duration_not_sliced(self, tmp_path, monkeypatch):
        monkeypatch.setattr(asr, "probe_duration", lambda p: None)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        assert asr.slice_audio(audio, tmp_path) == [(audio, 0.0)]

    def test_long_audio_sliced_with_offsets(self, tmp_path, monkeypatch):
        monkeypatch.setattr(asr, "probe_duration", lambda p: 1300.0)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")

        def fake_run(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"slice")

            class P:
                returncode = 0

            return P()

        monkeypatch.setattr(asr.subprocess, "run", fake_run)
        slices = asr.slice_audio(audio, tmp_path, slice_sec=600)
        assert [off for _, off in slices] == [0.0, 600.0, 1200.0]


class TestLocalModelReuse:
    """本地模型必须跨切片复用。

    实测教训（P3-08 冒烟）：每片新建 faster-whisper 模型会在 Windows 上
    积累多个 ctranslate2 实例，第二片编码时死锁、进程永久挂起。
    """

    @pytest.fixture(autouse=True)
    def fake_whisper(self, monkeypatch, tmp_path):
        import types

        self.created: list[tuple[str, str]] = []
        self.gpu_broken = False
        outer = self

        class Seg:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeModel:
            def __init__(self, name, device="auto", compute_type="auto"):
                outer.created.append((device, compute_type))
                self.device = device

            def transcribe(self, source, **kw):
                if self.device != "cpu" and outer.gpu_broken:
                    raise RuntimeError("cublas64_12.dll not found")
                return iter([Seg(0.0, 2.0, "你好")]), {}

        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = FakeModel
        monkeypatch.setitem(sys.modules, "faster_whisper", fake)
        monkeypatch.setattr(asr, "_LOCAL_MODELS", {})
        # 两个假切片文件（内容不是合法 WAV，_load_wav_16k_mono 会退回传路径）
        self.s1 = tmp_path / "s1.wav"
        self.s2 = tmp_path / "s2.wav"
        self.s1.write_bytes(b"x")
        self.s2.write_bytes(b"x")

    def test_model_built_once_across_slices(self):
        asr._transcribe_local(self.s1)
        asr._transcribe_local(self.s2)
        assert len(self.created) == 1, "每个切片都新建模型会导致多实例死锁"

    def test_gpu_fallback_happens_once_then_cached(self):
        self.gpu_broken = True
        r1 = asr._transcribe_local(self.s1)
        r2 = asr._transcribe_local(self.s2)
        assert r1 == r2 == [(0.0, 2.0, "你好")]
        # 只允许：一次 auto 尝试 + 一次 CPU 回退；第二片不得再新建任何实例
        assert self.created == [("auto", "auto"), ("cpu", "int8")]

    def test_non_cuda_runtime_error_not_swallowed(self):
        class Boom:
            def __init__(self, *a, **kw):
                pass

            def transcribe(self, *a, **kw):
                raise RuntimeError("别的错误")

        sys.modules["faster_whisper"].WhisperModel = Boom
        with pytest.raises(RuntimeError, match="别的错误"):
            asr._transcribe_local(self.s1)


class TestOpenAIDialects:
    """云端两种方言：标准 /audio/transcriptions 与百炼 chat+Base64。"""

    @pytest.fixture(autouse=True)
    def fake_openai(self, monkeypatch, tmp_path):
        import types

        self.calls: dict = {}
        outer = self

        class Msg:
            content = "你好，世界。"

        class Choice:
            message = Msg()

        class ChatResp:
            choices = [Choice()]

        class Seg:
            start, end, text = 1.0, 2.0, "hello"

        class TransResp:
            segments = [Seg()]
            text = "hello"

        class Chat:
            def create(self, **kw):
                outer.calls["chat"] = kw
                return ChatResp()

        class Audio:
            def create(self, **kw):
                outer.calls["audio"] = kw
                return TransResp()

        class FakeClient:
            def __init__(self, **kw):
                outer.calls["client"] = kw
                self.chat = types.SimpleNamespace(completions=Chat())
                self.audio = types.SimpleNamespace(transcriptions=Audio())

        fake = types.ModuleType("openai")
        fake.OpenAI = FakeClient
        monkeypatch.setitem(sys.modules, "openai", fake)
        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "test-key-not-real")
        self.wav = tmp_path / "s.wav"
        self.wav.write_bytes(b"RIFFfake-wav-bytes")

    def test_dashscope_uses_chat_with_base64_data_uri(self, monkeypatch):
        monkeypatch.setenv("ASR_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        r = asr._transcribe_openai(self.wav)
        assert "audio" not in self.calls, "百炼没有 /audio/transcriptions，不能走文件接口"
        kw = self.calls["chat"]
        assert kw["model"] == "qwen3-asr-flash"
        data = kw["messages"][0]["content"][0]["input_audio"]["data"]
        assert data.startswith("data:audio/wav;base64,")
        # 百炼不返回时间戳：退化为切片级，不编造段内时间
        assert r == [(0.0, 0.0, "你好，世界。")]

    def test_standard_provider_uses_transcriptions(self, monkeypatch):
        monkeypatch.setenv("ASR_BASE_URL", "https://api.groq.com/openai/v1")
        r = asr._transcribe_openai(self.wav)
        assert "chat" not in self.calls
        assert self.calls["audio"]["model"] == "whisper-large-v3"
        assert r == [(1.0, 2.0, "hello")]

    def test_dashscope_slices_clamped_to_base64_limit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASR_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        monkeypatch.setattr(asr, "probe_duration", lambda p: 1300.0)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")

        def fake_run(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"slice")

            class P:
                returncode = 0

            return P()

        monkeypatch.setattr(asr.subprocess, "run", fake_run)
        seen: list[float] = []
        r = asr.transcribe_file(audio, transcribe_slice=lambda p: seen.append(0.0) or [(0.0, 0.0, "x")])
        assert r["ok"] and not r["partial"]
        # 1300 秒按 200 秒切：7 片，而不是标准方言的 3 片
        assert len(seen) == 7
        assert [s for s, _, _ in r["segments"]] == [0.0, 200.0, 400.0, 600.0, 800.0, 1000.0, 1200.0]

    def test_dashscope_oversize_slice_is_honest_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ASR_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        big = tmp_path / "big.wav"
        big.write_bytes(b"\0" * 8_000_000)
        with pytest.raises(ValueError, match="too large"):
            asr._transcribe_openai(big)
        reason, hint = asr._classify_asr_error(ValueError("audio too large for base64 limit"))
        assert reason == "network" and "时间段" in hint


class TestNativeDialect:
    """百炼原生协议（qwen-audio 系列）：路由、SSE 句级时间戳、诚实降级。"""

    @pytest.fixture(autouse=True)
    def env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
        monkeypatch.setenv("ASR_API_KEY", "test-key-not-real")
        monkeypatch.setenv("ASR_BASE_URL", "https://ws-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        monkeypatch.setenv("ASR_MODEL", "qwen-audio-3.0-asr-flash")
        self.wav = tmp_path / "s.wav"
        self.wav.write_bytes(b"RIFFfake-wav-bytes")
        self.posts: list = []
        # 路由守卫：本类只允许走原生接口，走错立刻失败而不是真联网
        monkeypatch.setattr(asr, "_transcribe_openai", lambda p: pytest.fail("路由错误：qwen-audio 走了兼容接口"))

    def _stub_post(self, monkeypatch, ctype, body):
        def fake(url, payload, api_key):
            self.posts.append((url, payload, api_key))
            return ctype, body

        monkeypatch.setattr(asr, "_native_post", fake)

    def test_routes_by_model_name_and_builds_native_payload(self, monkeypatch):
        self._stub_post(monkeypatch, "application/json", '{"output":{"text":"你好"}}')
        r = asr._transcribe_native(self.wav)
        url, payload, key = self.posts[0]
        assert url == (
            "https://ws-example.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/aigc/multimodal-generation/generation"
        )
        assert key == "test-key-not-real"
        assert payload["model"] == "qwen-audio-3.0-asr-flash"
        # format 是原生接口必选参数，漏了会 400 空响应
        assert payload["parameters"] == {"format": "wav", "sample_rate": "16000"}
        item = payload["input"]["messages"][0]["content"][0]
        assert item["type"] == "input_audio"
        assert item["input_audio"]["data"].startswith("data:audio/wav;base64,")
        assert r == [(0.0, 0.0, "你好")]

    def test_sse_sentence_timestamps(self, monkeypatch):
        body = "\n".join(
            [
                "id:1",
                "event:result",
                ":HTTP_STATUS/200",
                'data:{"output":{"sentence":{"sentence_id":1,"sentence_end":false,"begin_time":240,"text":"你"}}}',
                "id:2",
                'data:{"output":{"sentence":{"sentence_id":1,"sentence_end":true,"begin_time":240,"end_time":3000,"text":"你好。"},"text":"你好。"}}',
                "id:3",
                'data:{"output":{"sentence":{"sentence_id":2,"sentence_end":true,"begin_time":3200,"end_time":6100,"text":"世界。"},"text":"你好。世界。"}}',
            ]
        )
        self._stub_post(monkeypatch, "text/event-stream", body)
        # 中间事件（sentence_end=false）不收；毫秒换算成秒
        assert asr._transcribe_native(self.wav) == [(0.24, 3.0, "你好。"), (3.2, 6.1, "世界。")]

    def test_plain_json_without_sentences_degrades_to_slice_level(self, monkeypatch):
        self._stub_post(monkeypatch, "application/json", '{"output":{"text":"整段文本"}}')
        assert asr._transcribe_native(self.wav) == [(0.0, 0.0, "整段文本")]

    def test_aggregated_sentence_with_words_regroups_to_sentences(self, monkeypatch):
        # 实测形态：非流式只给一个跨整片的聚合句，但带词级毫秒时间戳
        words = [
            {"text": "你", "punctuation": "", "begin_time": 240, "end_time": 500},
            {"text": "好", "punctuation": "，", "begin_time": 500, "end_time": 900},
            {"text": "世", "punctuation": "", "begin_time": 900, "end_time": 1200},
            {"text": "界", "punctuation": "。", "begin_time": 1200, "end_time": 1500},
            {"text": "再见", "punctuation": "", "begin_time": 2000, "end_time": 2600},
        ]
        body = json.dumps({"output": {"text": "你好，世界。再见", "sentence": {"sentence_end": True, "begin_time": 240, "end_time": 2600, "text": "你好，世界。再见", "words": words}}})
        self._stub_post(monkeypatch, "application/json", body)
        # 空标点不是句末（"" in 字符串恒真，曾导致逐词断句）
        assert asr._transcribe_native(self.wav) == [(0.24, 1.5, "你好，世界。"), (2.0, 2.6, "再见")]

    def test_transcribe_file_routes_native_and_keeps_offsets(self, monkeypatch):
        body = 'data:{"output":{"sentence":{"sentence_id":1,"sentence_end":true,"begin_time":240,"end_time":3000,"text":"你好。"}}}'
        self._stub_post(monkeypatch, "text/event-stream", body)
        seen: list[int] = []

        def fake_slice(audio, work, sec):
            seen.append(sec)
            return [(self.wav, 0.0), (self.wav, 200.0), (self.wav, 400.0)]

        monkeypatch.setattr(asr, "slice_audio", fake_slice)
        r = asr.transcribe_file(self.wav)
        assert r["ok"] and not r["partial"]
        # 走原生而非兼容接口（兼容接口在这个环境里没有 openai 打桩，走错会失败）
        assert len(self.posts) == 3
        assert seen == [200], "原生方言同样受 Base64 10MB 限制，切片上限 200 秒"
        assert [s for s, _, _ in r["segments"]] == [0.24, 200.24, 400.24]

    def test_native_oversize_is_honest_error(self, tmp_path):
        big = tmp_path / "big.wav"
        big.write_bytes(b"\0" * 8_000_000)
        with pytest.raises(ValueError, match="too large"):
            asr._transcribe_native(big)

    def test_native_401_maps_to_login_wall(self, monkeypatch):
        import urllib.error

        def fake(url, payload, api_key):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)

        monkeypatch.setattr(asr, "_native_post", fake)
        monkeypatch.setattr(asr, "slice_audio", lambda a, w, s: [(self.wav, 0.0)])
        r = asr.transcribe_file(self.wav)
        assert not r["ok"] and r["error_reason"] == "login_wall"

    def test_native_partial_failure_keeps_earlier_slices(self, monkeypatch):
        import urllib.error

        state = {"n": 0}

        def fake(url, payload, api_key):
            state["n"] += 1
            if state["n"] == 2:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)
            return (
                "text/event-stream",
                'data:{"output":{"sentence":{"sentence_id":1,"sentence_end":true,"begin_time":0,"end_time":1000,"text":"前半。"}}}',
            )

        monkeypatch.setattr(asr, "_native_post", fake)
        monkeypatch.setattr(asr, "slice_audio", lambda a, w, s: [(self.wav, 0.0), (self.wav, 200.0)])
        r = asr.transcribe_file(self.wav)
        assert r["ok"] and r["partial"] and r["error_reason"] == "anti_scrape"
        assert [s for s, _, _ in r["segments"]] == [0.0]