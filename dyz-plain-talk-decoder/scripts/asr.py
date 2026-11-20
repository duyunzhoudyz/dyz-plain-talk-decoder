"""可插拔 ASR：提供方选择、音频切片、时间戳拼接（PRD 模块 A、5.2）。

提供方由环境变量选择：
- ASR_PROVIDER=openai_compatible：云端接口，按模型名自动路由三种方言——
  标准 OpenAI 兼容（OpenAI、Groq 等）、百炼兼容模式（qwen3-asr 系列）、
  百炼原生协议（qwen-audio 系列，句级时间戳）；地址用 ASR_BASE_URL 配置；
- ASR_PROVIDER=local_whisper：本地 faster-whisper，离线可用；
- 未设置：不启用 ASR，返回 asr_not_configured，不报错崩溃。

其他环境变量：ASR_API_KEY（云端必需）、ASR_BASE_URL（可选）、ASR_MODEL（可选）。
密钥只从环境变量读取，不写入文件、日志或返回结果。

云端接口有单文件大小限制：音频按固定时长切片后逐片转写，
再按偏移量拼回连续时间戳。切片用 ffmpeg，调用一律参数列表、不用 shell。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

# 切片时长（秒）：16kHz 单声道 WAV 每分钟约 1.9MB，600 秒约 19MB，
# 低于 OpenAI 兼容接口常见的 25MB 单文件上限
SLICE_SEC = 600
# 百炼（DashScope）两种方言都走 Base64 Data URI，编码后上限 10MB：
# 200 秒 WAV 约 6.4MB，编码后约 8.5MB，留有余量
DASHSCOPE_SLICE_SEC = 200
# 单片转写的超时（秒）：云端快、本地慢，统一给足
TRANSCRIBE_TIMEOUT = 900

PROVIDERS = ("openai_compatible", "local_whisper")


def _log(message: str) -> None:
    import sys

    print(message, file=sys.stderr)


def get_provider() -> tuple[str, str]:
    """读取提供方配置。返回 (提供方, 问题说明)，问题为空表示配置可用。"""
    provider = os.environ.get("ASR_PROVIDER", "").strip()
    if not provider:
        return "", "未配置 ASR"
    if provider not in PROVIDERS:
        return provider, "ASR_PROVIDER 只能是 openai_compatible 或 local_whisper"
    if provider == "openai_compatible" and not os.environ.get("ASR_API_KEY", "").strip():
        return provider, "使用云端接口需要设置环境变量 ASR_API_KEY"
    return provider, ""


def probe_duration(audio_path: Path) -> Optional[float]:
    """用 ffprobe 读音频时长（秒）。读不到返回 None。"""
    if not shutil.which("ffprobe"):
        return None
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return float(proc.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def slice_audio(audio_path: Path, work_dir: Path, slice_sec: int = SLICE_SEC) -> list[tuple[Path, float]]:
    """把音频按固定时长切片。返回 [(切片文件, 偏移秒)]。

    时长读不到或不足一片时不切，整文件作为唯一一片（偏移 0）。
    """
    duration = probe_duration(audio_path)
    if duration is None or duration <= slice_sec:
        return [(audio_path, 0.0)]
    slices: list[tuple[Path, float]] = []
    count = int(duration // slice_sec) + (1 if duration % slice_sec > 1 else 0)
    for i in range(count):
        offset = i * slice_sec
        target = work_dir / f"slice_{i:03d}.wav"
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{offset:.2f}", "-t", str(slice_sec),
                    "-i", str(audio_path), "-ac", "1", "-ar", "16000", str(target),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            break
        if proc.returncode != 0 or not target.exists():
            break
        slices.append((target, offset))
    return slices or [(audio_path, 0.0)]


def merge_segments(parts: list[list[Any]], offsets: list[float]) -> list[Any]:
    """把各切片的片段按偏移量拼回连续时间戳。

    片段可以是 (start, end, text) 元组或带 start/end/text 属性的对象，
    统一返回 (start, end, text) 元组列表。离线可测。
    """
    merged: list[tuple[float, float, str]] = []
    for segments, offset in zip(parts, offsets):
        for seg in segments:
            if isinstance(seg, (tuple, list)):
                start, end, text = float(seg[0]), float(seg[1]), str(seg[2])
            else:
                start, end, text = float(seg.start), float(seg.end), str(seg.text)
            text = text.strip()
            if text:
                merged.append((start + offset, end + offset, text))
    merged.sort(key=lambda s: s[0])
    return merged


# ------------------------------------------------------------ 各提供方的单片转写


def _dashscope_dialect(base_url: str, model: str) -> bool:
    """是否为百炼（DashScope）兼容模式的 ASR 方言。

    百炼的 OpenAI 兼容 ASR 走 chat/completions + input_audio（Base64 Data URI），
    不是 /audio/transcriptions 文件接口，且不返回时间戳。按地址或模型名识别。
    """
    host = (base_url or "").lower()
    name = (model or "").lower()
    return (
        "dashscope" in host
        or "maas.aliyuncs" in host
        or name.startswith("qwen3-asr")
    )


def _native_dialect(model: str) -> bool:
    """是否为百炼原生协议 ASR（qwen-audio 系列）。

    该系列只支持原生 multimodal-generation 接口，送进 compatible-mode 恒 400，
    因此按模型名路由（PRD 模块 C“ASR 可插拔”）。原生接口返回句级毫秒时间戳。
    """
    return (model or "").lower().startswith("qwen-audio")


def _transcribe_openai(slice_path: Path) -> list[tuple[float, float, str]]:
    """OpenAI 兼容接口单片转写，返回带时间戳的片段。

    标准方言（OpenAI、Groq 等）走 /audio/transcriptions，有分段时间戳；
    百炼方言走 chat/completions + Base64 音频，只返回整段文本，
    时间戳退化为切片级（起止都记切片偏移），不编造段内时间。
    """
    from openai import OpenAI  # 延迟导入

    base_url = os.environ.get("ASR_BASE_URL", "").strip() or None
    model = os.environ.get("ASR_MODEL", "").strip()
    dashscope = _dashscope_dialect(base_url or "", model)
    if not model:
        # 各家默认模型名不同
        if dashscope:
            model = "qwen3-asr-flash"
        else:
            model = "whisper-large-v3" if base_url and "groq" in base_url.lower() else "whisper-1"
    client = OpenAI(api_key=os.environ["ASR_API_KEY"], base_url=base_url, timeout=TRANSCRIBE_TIMEOUT)
    if dashscope:
        import base64

        size = slice_path.stat().st_size
        if size > 7_500_000:
            # 编码后会超 10MB 上限；正常切片不会走到这里（ffprobe 缺失等例外）
            raise ValueError(f"audio too large for base64 limit: {size} bytes")
        mime = "audio/wav" if slice_path.suffix.lower() == ".wav" else "audio/mpeg"
        data_uri = f"data:{mime};base64,{base64.b64encode(slice_path.read_bytes()).decode()}"
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}]}],
            extra_body={"asr_options": {"enable_itn": True}},
        )
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        return [(0.0, 0.0, text)] if text.strip() else []
    with slice_path.open("rb") as fh:
        resp = client.audio.transcriptions.create(model=model, file=fh, response_format="verbose_json")
    segments = getattr(resp, "segments", None) or []
    if segments:
        return [(float(s["start"] if isinstance(s, dict) else s.start),
                 float(s["end"] if isinstance(s, dict) else s.end),
                 str(s["text"] if isinstance(s, dict) else s.text)) for s in segments]
    # 个别兼容服务不返回分段，只返回整段文本
    text = getattr(resp, "text", "") or ""
    return [(0.0, 0.0, text)] if text.strip() else []


def _native_post(url: str, payload: dict[str, Any], api_key: str) -> tuple[str, str]:
    """POST 百炼原生接口，返回 (Content-Type, 响应体)。只用标准库，不依赖 openai SDK。

    单独成函数便于离线测试打桩。SSE 响应整体读回再逐行解析：
    切片上限 200 秒，响应体最多几百 KB，不需要真流式消费。
    """
    import urllib.request

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "enable",
        },
    )
    with urllib.request.urlopen(req, timeout=TRANSCRIBE_TIMEOUT) as resp:
        ctype = resp.headers.get("Content-Type", "")
        return ctype, resp.read().decode("utf-8", "replace")


def _sentences_from_words(words: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """用词级毫秒时间戳按句末标点还原句级时间轴。

    实测 qwen-audio 系列非流式响应只给一个跨整片的聚合句，但带完整 words
    时间戳；按句号/问号/叹号/分号断句，时间戳全部来自接口，不估算。
    """
    segments: list[tuple[float, float, str]] = []
    buf: list[str] = []
    start: Optional[float] = None
    end = 0.0
    for word in words:
        text = str(word.get("text") or "")
        if not text:
            continue
        if start is None:
            start = float(word.get("begin_time") or 0) / 1000.0
        end = float(word.get("end_time") or 0) / 1000.0
        punctuation = str(word.get("punctuation") or "")
        buf.append(text + punctuation)
        if punctuation and punctuation in "。！？；?!;":
            segments.append((start, end, "".join(buf)))
            buf = []
            start = None
    if buf and start is not None:
        segments.append((start, end, "".join(buf)))
    return segments


def _parse_native_response(content_type: str, body: str) -> list[tuple[float, float, str]]:
    """把原生接口响应解析成句级 (开始秒, 结束秒, 文本) 片段。

    SSE 流取 sentence_end 为 true 的句子事件（毫秒时间戳换算成秒），
    按 sentence_id 去重（中间事件与最终事件可能重复同一句）。
    带词级时间戳时优先按句末标点还原句级时间轴（非流式聚合句即此形态）。
    接口只给整段文本、没有句子也没有词时，诚实退化为切片级 (0, 0)，不编造段内时间。
    """
    events: list[dict[str, Any]] = []
    if "event-stream" in content_type or body.lstrip().startswith(("id:", "data:")):
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
    else:
        try:
            events.append(json.loads(body))
        except ValueError:
            return []
    sentences: dict[int, tuple[float, float, str]] = {}
    words_by_sid: dict[int, list[dict[str, Any]]] = {}
    whole = ""
    for ev in events:
        out = ev.get("output") or {}
        whole = out.get("text") or whole
        sen = out.get("sentence") or {}
        sid = int(sen.get("sentence_id") or len(sentences) + 1)
        if sen.get("sentence_end") and str(sen.get("text", "")).strip():
            sentences[sid] = (
                float(sen.get("begin_time") or 0) / 1000.0,
                float(sen.get("end_time") or 0) / 1000.0,
                str(sen["text"]),
            )
        if sen.get("words"):
            # 同一句的中间事件与最终事件会重复带 words，只留最后一份
            words_by_sid[sid] = sen["words"]
    # 词级时间戳最细：非流式的聚合句靠它还原句级时间轴，SSE 句事件用它核对
    if words_by_sid:
        all_words = [w for sid in sorted(words_by_sid) for w in words_by_sid[sid]]
        grouped = _sentences_from_words(all_words)
        if grouped:
            return grouped
    if sentences:
        return [sentences[sid] for sid in sorted(sentences)]
    return [(0.0, 0.0, whole)] if whole.strip() else []


def _transcribe_native(slice_path: Path) -> list[tuple[float, float, str]]:
    """百炼原生 multimodal-generation 单片转写（qwen-audio 系列）。

    与兼容模式不同：原生协议返回句级毫秒时间戳，拼回后是真正的逐句时间轴。
    接口地址由 ASR_BASE_URL 的域名推导（空间专属域名或公共域名均可）。
    """
    import base64
    from urllib.parse import urlsplit

    size = slice_path.stat().st_size
    if size > 7_500_000:
        # 编码后会超 10MB 上限；正常切片不会走到这里（ffprobe 缺失等例外）
        raise ValueError(f"audio too large for base64 limit: {size} bytes")
    is_wav = slice_path.suffix.lower() == ".wav"
    mime = "audio/wav" if is_wav else "audio/mpeg"
    data_uri = f"data:{mime};base64,{base64.b64encode(slice_path.read_bytes()).decode()}"
    base_url = os.environ.get("ASR_BASE_URL", "").strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    parts = urlsplit(base_url)
    host = parts.netloc or "dashscope.aliyuncs.com"
    url = f"{parts.scheme or 'https'}://{host}/api/v1/services/aigc/multimodal-generation/generation"
    payload = {
        "model": os.environ.get("ASR_MODEL", "").strip(),
        "input": {
            "messages": [
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}]}
            ]
        },
        # format 为原生接口必选参数，漏了会 400 空响应
        "parameters": {"format": "wav" if is_wav else "mp3", "sample_rate": "16000"},
    }
    ctype, body = _native_post(url, payload, os.environ["ASR_API_KEY"])
    return _parse_native_response(ctype, body)


def _load_wav_16k_mono(path: Path) -> Optional[Any]:
    """用标准库读我们自己转出的 16kHz 单声道 WAV，返回 float32 数组。

    不走 faster-whisper 自带的 PyAV 解码：实测 av 19 与 faster-whisper 1.2.1
    的 decode_audio 不兼容（metadata_errors 参数报错）。读不了返回 None，
    由调用方退回传文件路径。
    """
    import wave

    try:
        import numpy as np
    except ImportError:
        return None
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != 16000:
                return None
            raw = w.readframes(w.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except (wave.Error, EOFError, OSError, ValueError):
        return None


# 本地模型进程内缓存：{模型名: 模型实例}。
# 实测（Windows + ctranslate2 4.9）：每个切片都新建模型会积累多个推理实例，
# 第二片在语言检测的编码步骤死锁（CPU 零占用、永久挂起）。
# 模型只建一次、跨切片复用；GPU 回退也只发生一次。
_LOCAL_MODELS: dict[str, Any] = {}


def _get_local_model(model_name: str) -> Any:
    """取得（必要时创建）本地模型，优先 GPU。结果缓存，进程内只建一次。"""
    from faster_whisper import WhisperModel  # 延迟导入

    if model_name not in _LOCAL_MODELS:
        _LOCAL_MODELS[model_name] = WhisperModel(model_name, device="auto", compute_type="auto")
    return _LOCAL_MODELS[model_name]


def _transcribe_local(slice_path: Path) -> list[tuple[float, float, str]]:
    """本地 faster-whisper 单片转写。模型跨切片复用，GPU 不可用时自动回退 CPU。"""
    model_name = os.environ.get("ASR_MODEL", "").strip() or "small"
    audio = _load_wav_16k_mono(slice_path)
    source = audio if audio is not None else str(slice_path)
    model = _get_local_model(model_name)
    try:
        segments, _info = model.transcribe(source, vad_filter=True)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "cuda" not in message and "cublas" not in message:
            raise
        # 有 GPU 但缺 CUDA 运行库：回退 CPU，不让用户去装几个 GB 的 CUDA。
        # 回退后的模型同样缓存，后续切片不再新建实例（多实例会死锁，见上）
        _log("[asr] GPU 运行库不可用，回退 CPU 转写")
        del model
        model = _build_cpu_model(model_name)
        _LOCAL_MODELS[model_name] = model
        segments, _info = model.transcribe(source, vad_filter=True)
    return [(float(s.start), float(s.end), str(s.text)) for s in segments]


def _build_cpu_model(model_name: str) -> Any:
    """建 CPU int8 模型。单独成函数便于测试打桩。"""
    from faster_whisper import WhisperModel  # 延迟导入

    return WhisperModel(model_name, device="cpu", compute_type="int8")


def _classify_asr_error(exc: Exception) -> tuple[str, str]:
    """把 ASR 的异常映射到 PRD 5.1 的 error_reason 与用户指引。不输出密钥。"""
    name = type(exc).__name__
    message = str(exc)
    low = message.lower()
    if isinstance(exc, ImportError):
        missing = "openai" if "openai" in message else "faster-whisper"
        return "missing_dependency", f"语音转文字缺少组件 {missing}。运行：pip install {missing}；或把字幕/文字稿贴给我。"
    if "authentication" in name.lower() or "401" in low or "invalid api key" in low or "permission" in name.lower():
        return "login_wall", "云端转写接口拒绝了请求，请检查环境变量 ASR_API_KEY 是否正确、有没有额度。"
    if "rate" in name.lower() or "429" in low:
        return "anti_scrape", "云端转写接口限流了。请稍后再试，或改用本地转写（ASR_PROVIDER=local_whisper）。"
    if "too large" in low or "413" in low:
        return "network", "音频文件超过接口限制。可以缩短时间段（--start/--end）后重试。"
    if "400" in low:
        return "network", (
            "云端转写接口拒绝了请求。请检查 ASR_MODEL 与接口是否匹配："
            "百炼 qwen-audio 系列走原生协议，qwen3-asr 与其他模型走兼容接口。"
        )
    # 异常信息可能回显密钥，日志只记异常类型，不记内容
    _log(f"[asr] 转写失败：{name}")
    return "network", "语音转写没有成功，可能是网络或接口问题。可以稍后再试，或把字幕/文字稿贴给我。"


# ------------------------------------------------------------ 对外入口


def transcribe_file(
    audio_path: Path,
    *,
    offset_sec: float = 0.0,
    slice_sec: int = SLICE_SEC,
    transcribe_slice: Optional[Callable[[Path], list[tuple[float, float, str]]]] = None,
) -> dict[str, Any]:
    """转写一个音频文件，返回 {ok, segments, error_reason, user_hint}。

    segments 为 (start, end, text) 元组列表，时间戳已含 offset_sec 与切片偏移。
    `transcribe_slice` 仅供测试注入打桩的转写函数。
    任何预期内的失败都转成 error_reason + user_hint，不抛堆栈。
    """
    provider, problem = get_provider()
    if problem:
        return {
            "ok": False, "segments": [], "error_reason": "asr_not_configured",
            "user_hint": (
                f"语音转文字（ASR）配置有问题：{problem}。"
                "配置方法见 README；也可以直接把字幕或文字稿贴给我。"
            ),
        }

    if transcribe_slice is None:
        if provider == "local_whisper":
            transcribe_slice = _transcribe_local
        elif _native_dialect(os.environ.get("ASR_MODEL", "")):
            transcribe_slice = _transcribe_native
        else:
            transcribe_slice = _transcribe_openai
    if provider == "openai_compatible" and (
        _native_dialect(os.environ.get("ASR_MODEL", ""))
        or _dashscope_dialect(os.environ.get("ASR_BASE_URL", ""), os.environ.get("ASR_MODEL", ""))
    ):
        # 百炼两种方言都走 Base64，上限 10MB：切片必须比标准方言更短
        slice_sec = min(slice_sec, DASHSCOPE_SLICE_SEC)

    audio_path = Path(audio_path)
    with tempfile.TemporaryDirectory(prefix="ptd-asr-") as tmp:
        slices = slice_audio(audio_path, Path(tmp), slice_sec)
        parts: list[list[Any]] = []
        offsets: list[float] = []
        for slice_path, slice_offset in slices:
            try:
                segments = transcribe_slice(slice_path)
            except Exception as exc:
                reason, hint = _classify_asr_error(exc)
                if parts:
                    # 已转出一部分：如实返回部分结果，不冒充完整
                    merged = merge_segments(parts, [o + offset_sec for o in offsets])
                    return {
                        "ok": True, "partial": True, "segments": merged,
                        "error_reason": reason,
                        "user_hint": f"只转写了前面一部分，后面失败了：{hint}",
                    }
                return {"ok": False, "segments": [], "error_reason": reason, "user_hint": hint}
            parts.append(segments)
            offsets.append(slice_offset)
            _log(f"[asr] 切片 {slice_offset:.0f}s 起：{len(segments)} 段")
    merged = merge_segments(parts, [o + offset_sec for o in offsets])
    return {"ok": True, "partial": False, "segments": merged, "error_reason": "", "user_hint": ""}