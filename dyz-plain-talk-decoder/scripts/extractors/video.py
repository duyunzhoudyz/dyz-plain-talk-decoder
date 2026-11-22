"""视频省流漏斗 L0–L3（PRD 模块 A）。

原则：每一层都比下一层便宜一个量级，能在当前层拿到足够内容就停。
- L0 元数据：yt-dlp 取标题、时长、有无字幕，不下载任何媒体；
- L1 原生字幕：人工字幕 > 平台 CC > 自动字幕，质量合格就停；
- L2 音频 + ASR：只下载最低码率音频，转写为带时间戳的逐字稿；
- L3 按需抽帧：仅在明确要求时下载 ≤480p 视频，场景检测抽关键帧。

红线：不得仅凭标题和简介解码；未配置 ASR 不崩溃，返回 partial 与指引；
超过时长闸门先征求同意；缓存只放系统临时目录。
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .base import USER_HINTS, failed_result, http_get, log, make_result

# 视频平台域名 -> 平台代号（fetch_content 按它路由）
VIDEO_HOSTS = {
    "bilibili.com": "bilibili",
    "b23.tv": "bilibili",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "douyin.com": "douyin",
    "iesdouyin.com": "douyin",
    "channels.weixin.qq.com": "wechat_channels",  # 视频号
}

# 本地音视频文件扩展名（直接走 L2/L3，不经过 yt-dlp 下载）
LOCAL_AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}
LOCAL_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv"}
LOCAL_MEDIA_EXTS = LOCAL_AUDIO_EXTS | LOCAL_VIDEO_EXTS

# L0 + L1 的时间预算。目标是 10 秒内完成，但慢网络下元数据本身可能就要几秒，
# 预算太紧会把“有字幕”误判成失败，所以放宽到与图文一致的 20 秒
L01_BUDGET = 20
# 逐字稿与关键帧索引自写入起有效 24 小时；读取不续期，过期只当未命中，不删除文件。
CACHE_TTL_SEC = 24 * 60 * 60
# 时长闸门（秒）：超过 30 分钟需用户同意；超过 120 分钟默认不转写
LONG_GATE_SEC = 30 * 60
HARD_GATE_SEC = 120 * 60
# 抽帧参数（PRD：场景阈值 0.35、最小间隔 ≥3 秒、上限 12–16 帧、长边 ≤1024）
SCENE_THRESHOLD = 0.35
# 软切换（幻灯片换页一类）：分数远低于硬切，但换页后画面会静止几秒。
# 硬切一帧都抽不到时，用低阈值全量扫描 + “变化后静止”判定补抽。
# 阈值 0.02 来自实测：静态画面压缩噪声 ≤0.015，换页软过渡 0.02–0.13。
SCENE_SCAN_FLOOR = 0.02  # 扫描阈值，低于此视为压缩噪声
SCENE_QUIET_SEC = 2.0  # 换页后的静止观察窗口
FRAME_MIN_GAP = 3.0
FRAME_MAX_COUNT = 16
FRAME_LONG_SIDE = 1024
# 音频切片时长（秒）：16kHz 单声道 WAV 每分钟约 1.9MB，600 秒约 19MB，
# 低于 OpenAI 兼容接口常见的 25MB 单文件上限
AUDIO_SLICE_SEC = 600

# 字幕语言优先级：先中文，再英文，再视频原语言，最后其他
LANG_PRIORITY = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "en", "en-US", "en-GB"]
# 弹幕不是字幕
NON_SUBTITLE_LANGS = {"danmaku"}
# 只取我们能解析的字幕格式
PARSABLE_EXTS = ["json3", "vtt", "srv3", "json"]


@dataclass
class Segment:
    """一条带时间戳的字幕/逐字稿片段。"""

    start: float
    end: float
    text: str


# ------------------------------------------------------------ 缓存


def cache_root() -> Path:
    """缓存根目录：系统临时目录下的 plain-talk-decoder/。"""
    return Path(tempfile.gettempdir()) / "plain-talk-decoder"


def cache_dir_for(url: str, start_sec: Optional[float] = None, end_sec: Optional[float] = None) -> Path:
    """同一链接（同一时间段）一个缓存目录，按链接哈希命名。"""
    start = float(start_sec) if start_sec is not None else None
    end = float(end_sec) if end_sec is not None else None
    key = url + f"|{start}|{end}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return cache_root() / digest


def _read_cached_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            # 同一打开句柄上检查时间并读取，不能错用另一个进程刚替换进来的文件时间。
            if not 0 <= time.time() - os.fstat(fh.fileno()).st_mtime <= CACHE_TTL_SEC:
                return None
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _transcript_digest(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _frame_digest(path: Path) -> str:
    """核对 JPEG 边界与内容；不把半张图、同名替换或残缺写入当成原帧。"""
    try:
        # 输出的长边至多 1024，远小于这个上限；损坏的缓存不能耗尽内存。
        with path.open("rb") as fh:
            data = fh.read(8 * 1024 * 1024 + 1)
    except OSError:
        return ""
    if len(data) > 8 * 1024 * 1024 or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        return ""
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------ 时间解析与格式化


def parse_time_spec(spec: str) -> float:
    """把 '00:10:00' / '10:00' / '600' 解析成秒。格式不对抛 ValueError。"""
    text = (spec or "").strip()
    if not text:
        raise ValueError("空的时间")
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"无法识别的时间：{spec}")
    seconds = 0.0
    for part in parts:
        if not re.fullmatch(r"\d+(\.\d+)?", part):
            raise ValueError(f"无法识别的时间：{spec}")
        seconds = seconds * 60 + float(part)
    return seconds


def format_timestamp(seconds: float) -> str:
    """秒 -> 'mm:ss'，超过一小时用 'h:mm:ss'。"""
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_transcript(segments: list[Segment]) -> str:
    """片段列表 -> 带时间戳的逐字稿文本，合并连续重复行。"""
    lines: list[str] = []
    prev_text = ""
    for seg in segments:
        text = seg.text.strip()
        if not text or text == prev_text:
            continue
        lines.append(f"[{format_timestamp(seg.start)}] {text}")
        prev_text = text
    return "\n".join(lines)


# ------------------------------------------------------------ 字幕解析（离线可测）


def _vtt_time(text: str) -> float:
    parts = text.strip().split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_vtt(text: str) -> list[Segment]:
    """解析 WebVTT 字幕。去掉标签、提示行与连续重复（滚动字幕常见）。"""
    segments: list[Segment] = []
    prev_text = ""
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        if not lines:
            continue
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if timing_idx < 0:
            continue  # WEBVTT 头、NOTE、STYLE 等
        timing = lines[timing_idx].split("-->")
        try:
            start = _vtt_time(timing[0])
            # 结束时间后面可能跟 cue 设置（align:start 等），只取第一段
            end = _vtt_time(timing[1].strip().split(" ")[0])
        except (ValueError, IndexError):
            continue
        body = " ".join(lines[timing_idx + 1 :])
        body = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        if not body or body == prev_text:
            continue
        segments.append(Segment(start, end, body))
        prev_text = body
    return segments


def parse_json3(data: dict[str, Any]) -> list[Segment]:
    """解析 YouTube json3 字幕。"""
    segments: list[Segment] = []
    prev_text = ""
    for event in data.get("events") or []:
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not text or text == prev_text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000.0
        end = start + float(event.get("dDurationMs", 0)) / 1000.0
        segments.append(Segment(start, end, text))
        prev_text = text
    return segments


def parse_srv3(text: str) -> list[Segment]:
    """解析 YouTube srv3（XML）字幕。"""
    segments: list[Segment] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return segments
    for node in root.iter("text"):
        body = html.unescape("".join(node.itertext())).replace("\n", " ").strip()
        if not body:
            continue
        start = float(node.get("start", "0"))
        end = start + float(node.get("dur", "0"))
        segments.append(Segment(start, end, body))
    return segments


def parse_bili_json(data: dict[str, Any]) -> list[Segment]:
    """解析 B站字幕（body: [{from, to, content}]）。"""
    segments: list[Segment] = []
    for item in data.get("body") or []:
        text = str(item.get("content", "")).strip()
        if not text:
            continue
        segments.append(Segment(float(item.get("from", 0)), float(item.get("to", 0)), text))
    return segments


def parse_subtitle(text: str, ext: str) -> list[Segment]:
    """按格式选解析器。解析不了返回空列表，由上层按质量不合格处理。"""
    try:
        if ext == "vtt":
            return parse_vtt(text)
        if ext == "srv3":
            return parse_srv3(text)
        data = json.loads(text)
        if ext == "json3":
            return parse_json3(data)
        if ext == "json":
            return parse_bili_json(data)
    except (ValueError, TypeError) as exc:
        log(f"[subtitle] {ext} 解析失败：{type(exc).__name__}")
    return []


def pick_subtitle_track(info: dict[str, Any]) -> Optional[dict[str, str]]:
    """按“人工字幕 > 平台 CC > 自动字幕”选一条可解析的字幕轨。

    yt-dlp 把人工与平台 CC 都放在 subtitles，自动字幕在 automatic_captions。
    返回 {lang, ext, url, auto} 或 None。
    """
    original_lang = info.get("language") or ""
    for auto, source in ((False, info.get("subtitles") or {}), (True, info.get("automatic_captions") or {})):
        langs = [ln for ln in source if ln not in NON_SUBTITLE_LANGS]
        ordered = (
            [ln for ln in LANG_PRIORITY if ln in langs]
            + ([original_lang] if original_lang in langs and original_lang not in LANG_PRIORITY else [])
            + sorted(ln for ln in langs if ln not in LANG_PRIORITY and ln != original_lang)
        )
        for lang in ordered:
            formats = source.get(lang) or []
            for ext in PARSABLE_EXTS:
                for fmt in formats:
                    if fmt.get("ext") == ext and fmt.get("url"):
                        return {"lang": lang, "ext": ext, "url": fmt["url"], "auto": "1" if auto else ""}
    return None


def check_subtitle_quality(segments: list[Segment], duration_sec: float) -> tuple[bool, str]:
    """字幕质量检查（P3-02）。不合格才进 L2。

    检查：过短、覆盖时长不足、大量乱码、大量重复。
    返回 (合格与否, 不合格原因)。纯音乐/BGM 视频字幕本来就短，会被判不合格，符合预期。
    """
    text = "".join(seg.text for seg in segments)
    n = len(text.strip())
    if not segments or n == 0:
        return False, "没有字幕内容"
    # 过短：按正常语速的保守下限（中文约 3–5 字/秒，取 0.8 字/秒兜底）
    min_chars = max(40, duration_sec * 0.8)
    if n < min_chars:
        return False, f"字幕只有 {n} 字，相对视频时长明显过短"
    # 覆盖不足：字幕只盖住开头一小段（长视频才检查，短视频容忍片尾无字幕）
    if duration_sec > 60:
        covered = max(seg.end for seg in segments)
        if covered < duration_sec * 0.5:
            return False, f"字幕只覆盖到 {format_timestamp(covered)}，不足视频时长的一半"
    # 大量重复：自动字幕故障时常见整屏复读
    lines = [seg.text.strip() for seg in segments if seg.text.strip()]
    if len(lines) >= 20 and len(set(lines)) / len(lines) < 0.3:
        return False, "字幕大量重复，疑似损坏"
    # 大量乱码
    bad = sum(1 for ch in text if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\n\t"))
    if bad / max(1, n) > 0.05:
        return False, "字幕包含大量乱码"
    return True, ""


# ------------------------------------------------------------ 时长闸门（离线可测）


def gate_decision(
    duration_sec: float,
    *,
    allow_long: bool,
    start_sec: Optional[float],
    end_sec: Optional[float],
    provider: str,
) -> tuple[str, str]:
    """决定能否进入 L2 转写。返回 (动作, 说明)。

    动作：proceed 直接转写 / confirm 需用户同意 / refuse 默认不转写。
    指定了时间段时按时间段长度计算。
    """
    if start_sec is not None or end_sec is not None:
        effective = (end_sec if end_sec is not None else duration_sec) - (start_sec or 0.0)
    else:
        effective = duration_sec
    effective = max(0.0, effective)

    if effective > HARD_GATE_SEC and (start_sec is None and end_sec is None):
        return "refuse", (
            f"视频长约 {int(duration_sec // 60)} 分钟，超过 2 小时，默认不做整段转写。"
            "建议把字幕或文字稿贴给我，或用 --start/--end 指定要看的时间段。"
        )
    if effective > LONG_GATE_SEC and not allow_long:
        if provider == "local_whisper":
            estimate = f"约 {max(1, round(effective * 0.5 / 60))}–{max(2, round(effective * 3 / 60))} 分钟（取决于电脑性能）"
        else:
            estimate = f"约 {max(1, round(effective * 0.1 / 60))}–{max(2, round(effective * 0.5 / 60))} 分钟"
        return "confirm", (
            f"视频长约 {int(effective // 60)} 分钟，转写预计需要{estimate}，"
            "云端接口会按时长计费。请征求用户同意后用 --allow-long 重新调用，"
            "或用 --start/--end 只处理需要的时间段。"
        )
    return "proceed", ""


# ------------------------------------------------------------ 外部工具


def _run(cmd: list[str], timeout: float) -> tuple[bool, str]:
    """运行外部命令（参数列表，不用 shell）。返回 (成功与否, 标准错误内容)。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as exc:
        return False, type(exc).__name__
    return proc.returncode == 0, proc.stderr or ""


def _ytdlp_base_opts(cache_dir: Path) -> dict[str, Any]:
    """yt-dlp 公共参数：安静、不读 Cookie、失败少重试、缓存进临时目录。"""
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,  # 下载进度条会污染标准输出里的 JSON，必须关掉
        "noplaylist": True,
        "socket_timeout": 5,
        "retries": 1,  # 只对网络抖动重试 1 次
        "extractor_retries": 0,
        "cachedir": str(cache_dir / "ytdlp-cache"),
        "cookiefile": None,
        "nocheckcertificate": False,
    }


def _classify_ytdlp_error(message: str) -> tuple[str, str]:
    """把 yt-dlp 的报错映射到 PRD 5.1 的 error_reason 与用户指引。"""
    low = message.lower()
    if any(k in low for k in ("private", "login", "sign in", "members-only", "members only", "premium", "subscriber", "needs_auth", "account")):
        return "login_wall", "这个视频需要登录、会员或付费才能看。可以把字幕或文字稿贴给我。"
    if any(k in low for k in ("unavailable", "removed", "not exist", "deleted", "404", "no longer")):
        return "not_found", USER_HINTS["not_found"]
    if any(k in low for k in ("429", "too many requests", "captcha", "verify")):
        return "anti_scrape", USER_HINTS["anti_scrape"]
    if "403" in low:
        return "anti_scrape", USER_HINTS["anti_scrape"]
    return "network", (
        "视频信息读取失败，可能是网络问题或平台限制。可以先升级 yt-dlp 再试"
        "（pip install -U yt-dlp），或把字幕/文字稿贴给我。"
    )


def _upload_date(info: dict[str, Any]) -> str:
    raw = str(info.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return ""


# ------------------------------------------------------------ 提取器


class VideoExtractor:
    """视频省流漏斗。platform 由路由方传入（bilibili / youtube / douyin / wechat_channels / web）。"""

    def __init__(self, platform: str = "web") -> None:
        self.platform = platform

    def can_handle(self, url: str) -> bool:
        from .base import host_of

        host = host_of(url)
        return any(host == d or host.endswith("." + d) for d in VIDEO_HOSTS)

    # -------------------------------------------------- 对外入口

    def extract(
        self,
        url: str,
        deadline: float,
        *,
        frames: bool = False,
        allow_long: bool = False,
        start_sec: Optional[float] = None,
        end_sec: Optional[float] = None,
    ) -> dict[str, Any]:
        cache_dir = cache_dir_for(url, start_sec, end_sec)
        cache_dir.mkdir(parents=True, exist_ok=True)

        # 普通追问与抽帧共用同一份带完整性记录的逐字稿，不再付费转写。
        cached = self._load_transcript(cache_dir, url, start_sec, end_sec)
        if cached is not None:
            return self._l3_frames(url, cache_dir, cached, start_sec, end_sec) if frames else cached

        def finish_transcript(result: dict[str, Any]) -> dict[str, Any]:
            self._save_transcript(cache_dir, url, start_sec, end_sec, result)
            return self._l3_frames(url, cache_dir, result, start_sec, end_sec) if frames else result

        # L0 元数据
        info, error = self._l0_metadata(url, cache_dir, deadline)
        if error:
            reason, hint = error
            return failed_result(self.platform, url, reason, source_type="video", user_hint=hint)

        duration = float(info.get("duration") or 0)
        title = str(info.get("title") or "")
        author = str(info.get("uploader") or info.get("channel") or "")
        publish_date = _upload_date(info)

        def video_result(status: str, **kw: Any) -> dict[str, Any]:
            return make_result(
                status,
                source_type="video",
                platform=self.platform,
                url=info.get("webpage_url") or url,
                title=title,
                author=author,
                publish_date=publish_date,
                duration_sec=duration or None,
                **kw,
            )

        # L1 原生字幕
        segments, subtitle_note = self._l1_subtitles(info, cache_dir, deadline)
        if segments and (start_sec is not None or end_sec is not None):
            # 字幕是整条轨道：只保留与请求区间相交的字幕句，沿用原句时间戳。
            segments = [s for s in segments if (end_sec is None or s.start < end_sec)
                        and (start_sec is None or s.start >= start_sec or s.end > start_sec)]
            if not segments:
                subtitle_note = "指定时间段内没有字幕"
        if segments:
            return finish_transcript(video_result(
                "success", content_text=format_transcript(segments), extraction_method="subtitle", funnel_level="L1"
            ))

        # L2 音频 + ASR
        provider = os.environ.get("ASR_PROVIDER", "").strip()
        if provider not in ("openai_compatible", "local_whisper"):
            # 未配置 ASR：不崩溃，返回 partial 与两条路（PRD 模块 A）
            return video_result(
                "partial",
                content_text="",
                extraction_method="",
                funnel_level="L0",
                error_reason="asr_not_configured",
                user_hint=(
                    "这个视频没有可用字幕" + (f"（{subtitle_note}）" if subtitle_note else "") + "。"
                    "要听写内容需要配置语音转文字（ASR）：设置环境变量 ASR_PROVIDER 等，方法见 README；"
                    "也可以贴字幕或文字稿；如果关键信息在画面里，请发关键画面的截图。我不会只凭标题和简介下结论。"
                ),
            )

        action, message = gate_decision(
            duration, allow_long=allow_long, start_sec=start_sec, end_sec=end_sec, provider=provider
        )
        if action != "proceed":
            return video_result(
                "partial",
                content_text="",
                funnel_level="L0",
                needs_confirmation=message,
                user_hint=message,
            )

        transcript, error = self._l2_transcribe(url, cache_dir, start_sec, end_sec)
        reason, hint = error or ("", "")
        if not transcript:
            return video_result(
                "partial", funnel_level="L0", error_reason=reason,
                user_hint=hint + " 若关键信息在画面里，请发关键画面的截图。",
            )
        return finish_transcript(video_result(
            "partial" if error else "success", content_text=transcript, extraction_method="asr", funnel_level="L2",
            error_reason=reason, user_hint=hint,
        ))

    def extract_local(
        self,
        media_path: Path,
        *,
        frames: bool = False,
        allow_long: bool = False,
        start_sec: Optional[float] = None,
        end_sec: Optional[float] = None,
    ) -> dict[str, Any]:
        """本地音视频文件直传：绕过 yt-dlp，直接走时长闸门 + ffmpeg 转码 + ASR（视频按需 L3 抽帧）。"""
        resolved = media_path.resolve()
        if not resolved.is_file():
            return failed_result(
                "local", str(media_path), "not_found", source_type="video",
                user_hint="找不到这个本地音视频文件，请检查路径是否正确。",
            )
        ext = resolved.suffix.lower()
        is_video = ext in LOCAL_VIDEO_EXTS
        stat = resolved.stat()
        cache_key = f"local:{resolved}@{stat.st_mtime_ns}:{stat.st_size}"
        cache_dir = cache_dir_for(cache_key, start_sec, end_sec)
        cache_dir.mkdir(parents=True, exist_ok=True)

        cached = self._load_transcript(cache_dir, cache_key, start_sec, end_sec)
        if cached is not None:
            if frames:
                return self._l3_local_frames(resolved, is_video, cache_dir, cached, start_sec, end_sec)
            return cached

        import asr  # 延迟导入

        duration = asr.probe_duration(resolved) or 0.0

        def local_result(status: str, **kw: Any) -> dict[str, Any]:
            return make_result(
                status,
                source_type="video",
                platform="local",
                url=str(resolved),
                title=resolved.stem,
                duration_sec=duration or None,
                **kw,
            )

        provider = os.environ.get("ASR_PROVIDER", "").strip()
        if provider not in ("openai_compatible", "local_whisper"):
            return local_result(
                "partial",
                content_text="",
                extraction_method="",
                funnel_level="L0",
                error_reason="asr_not_configured",
                user_hint=(
                    "本地音视频需要配置语音转文字（ASR）才能转写：请设置环境变量 ASR_PROVIDER 等（方法见 README）；"
                    "也可以直接把文字稿贴给我。"
                ),
            )

        if not shutil.which("ffmpeg"):
            return local_result(
                "partial",
                content_text="",
                extraction_method="",
                funnel_level="L0",
                error_reason="missing_dependency",
                user_hint="转写本地音视频需要 ffmpeg。Windows 可用 winget install ffmpeg，macOS 可用 brew install ffmpeg。",
            )

        action, message = gate_decision(
            duration, allow_long=allow_long, start_sec=start_sec, end_sec=end_sec, provider=provider
        )
        if action != "proceed":
            return local_result(
                "partial",
                content_text="",
                funnel_level="L0",
                needs_confirmation=message,
                user_hint=message,
            )

        wav = cache_dir / "audio_16k.wav"
        if not wav.exists():
            cmd = ["ffmpeg", "-y"]
            if start_sec is not None:
                cmd += ["-ss", f"{start_sec:.2f}"]
            if end_sec is not None:
                cmd += ["-to", f"{end_sec:.2f}"]
            cmd += ["-i", str(resolved), "-vn", "-ac", "1", "-ar", "16000", str(wav)]
            ok, err = _run(cmd, timeout=300)
            if not ok:
                log(f"[local L2] ffmpeg 转码失败：{err[-300:]}")
                return local_result(
                    "partial",
                    funnel_level="L0",
                    error_reason="missing_dependency",
                    user_hint="音频转码失败，请确认文件未损坏且 ffmpeg 安装完整。",
                )

        tr = asr.transcribe_file(wav, offset_sec=start_sec or 0.0)
        if not tr.get("ok"):
            return local_result(
                "partial",
                funnel_level="L0",
                error_reason=tr.get("error_reason") or "network",
                user_hint=(tr.get("user_hint") or USER_HINTS["network"]) + " 也可以直接贴文字稿。",
            )
        segments = [Segment(start, end, text) for start, end, text in tr["segments"]]
        transcript = format_transcript(segments)
        if not transcript:
            return local_result(
                "partial",
                funnel_level="L0",
                error_reason="not_found",
                user_hint="转写没有得到文字，这个文件可能没有人声；若是无声录屏，请直接发关键画面截图。",
            )

        is_partial = bool(tr.get("partial"))
        reason = (tr.get("error_reason") or "network") if is_partial else ""
        hint = (tr.get("user_hint") or "只转写了文件的一部分。") if is_partial else ""
        result = local_result(
            "partial" if is_partial else "success",
            content_text=transcript,
            extraction_method="asr",
            funnel_level="L2",
            error_reason=reason,
            user_hint=hint,
        )
        self._save_transcript(cache_dir, cache_key, start_sec, end_sec, result)
        if frames:
            return self._l3_local_frames(resolved, is_video, cache_dir, result, start_sec, end_sec)
        return result

    def _l3_local_frames(
        self,
        media_path: Path,
        is_video: bool,
        cache_dir: Path,
        cached: dict[str, Any],
        start_sec: Optional[float],
        end_sec: Optional[float],
    ) -> dict[str, Any]:
        """本地视频文件按需抽帧（纯音频文件直接保留逐字稿并说明无画面）。"""
        if not is_video:
            note = "这是纯音频文件，没有画面可抽帧；已返回完整文字转写。"
            return dict(cached, user_hint=" ".join(filter(None, (cached["user_hint"], note))))
        if not cached.get("content_text"):
            return failed_result(
                "local", str(media_path), "not_found", source_type="video",
                user_hint="没有可用逐字稿，无法解读全片。请提供关键画面的截图或文字稿。",
            )
        if not shutil.which("ffmpeg"):
            return dict(
                cached,
                status="partial",
                funnel_level="L3",
                error_reason=cached["error_reason"] or "missing_dependency",
                user_hint=" ".join(filter(None, (cached["user_hint"], "抽帧需要 ffmpeg，逐字稿已拿到，可先用文字部分。"))),
            )
        return self._run_l3_on_file(media_path, cache_dir, cached, start_sec, end_sec)

    # -------------------------------------------------- L0

    def _l0_metadata(
        self, url: str, cache_dir: Path, deadline: float
    ) -> tuple[dict[str, Any], Optional[tuple[str, str]]]:
        """yt-dlp 取元数据，不下载任何媒体文件。"""
        try:
            import yt_dlp  # 延迟导入
        except ImportError:
            return {}, ("missing_dependency", "读取视频需要 yt-dlp。运行：pip install yt-dlp；或把字幕/文字稿贴给我。")
        if time.monotonic() >= deadline:
            return {}, ("network", USER_HINTS["network"])
        opts = _ytdlp_base_opts(cache_dir)
        opts["skip_download"] = True
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # yt-dlp 的异常种类很多，统一映射
            message = str(exc)
            log(f"[L0] yt-dlp 失败：{message[:300]}")
            return {}, _classify_ytdlp_error(message)
        if not info:
            return {}, ("not_found", USER_HINTS["not_found"])
        availability = str(info.get("availability") or "")
        if availability in ("private", "premium_only", "subscriber_only", "needs_auth"):
            return {}, ("login_wall", "这个视频需要登录、会员或付费才能看。可以把字幕或文字稿贴给我。")
        log(f"[L0] 标题={info.get('title')!r} 时长={info.get('duration')}s（未下载媒体）")
        return info, None

    # -------------------------------------------------- L1

    def _l1_subtitles(
        self, info: dict[str, Any], cache_dir: Path, deadline: float
    ) -> tuple[list[Segment], str]:
        """取字幕并做质量检查。返回 (合格的片段列表, 不合格说明)。"""
        track = pick_subtitle_track(info)
        if not track:
            return [], ""
        fetched = http_get(track["url"], deadline)
        if fetched.error_reason or not fetched.text:
            log(f"[L1] 字幕下载失败：{fetched.error_reason} {fetched.detail}")
            return [], "字幕下载失败"
        segments = parse_subtitle(fetched.text, track["ext"])
        duration = float(info.get("duration") or 0)
        ok, reason = check_subtitle_quality(segments, duration)
        if not ok:
            log(f"[L1] 字幕质量不合格：{reason}")
            return [], reason
        try:
            (cache_dir / "subtitle_raw.txt").write_text(fetched.text, encoding="utf-8")
        except OSError as exc:
            log(f"[L1] 原始字幕未能写入缓存：{type(exc).__name__}")
        log(f"[L1] 字幕合格：{len(segments)} 段，语言={track['lang']}，自动={track['auto'] or '否'}")
        return segments, ""

    # -------------------------------------------------- L2

    def _l2_transcribe(
        self,
        url: str,
        cache_dir: Path,
        start_sec: Optional[float],
        end_sec: Optional[float],
    ) -> tuple[str, Optional[tuple[str, str]]]:
        """下载最低码率音频并转写。非空逐字稿与错误并存时表示部分转写。"""
        try:
            import yt_dlp  # 延迟导入
        except ImportError:
            return "", ("missing_dependency", "读取视频需要 yt-dlp。运行：pip install yt-dlp。")
        if not shutil.which("ffmpeg"):
            return "", ("missing_dependency", "语音转写需要 ffmpeg。Windows 可用 winget install ffmpeg，macOS 可用 brew install ffmpeg。")

        # 只下载音频流，选最低码率；已有缓存则复用
        audio_files = sorted(cache_dir.glob("audio_raw.*"))
        if not audio_files:
            opts = _ytdlp_base_opts(cache_dir)
            opts.update(
                {
                    "format": "bestaudio[abr<=64]/worstaudio/bestaudio",
                    "outtmpl": str(cache_dir / "audio_raw.%(ext)s"),
                    "overwrites": True,
                }
            )
            if start_sec is not None or end_sec is not None:
                # 只下载指定时间段，不多下载
                opts["download_ranges"] = yt_dlp.utils.download_range_func(
                    None, [(start_sec or 0.0, end_sec if end_sec is not None else float("inf"))]
                )
                opts["force_keyframes_at_cuts"] = True
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
            except Exception as exc:
                log(f"[L2] 音频下载失败：{str(exc)[:300]}")
                return "", _classify_ytdlp_error(str(exc))
            audio_files = sorted(cache_dir.glob("audio_raw.*"))
            if not audio_files:
                return "", ("network", "音频下载没有产出文件。可以升级 yt-dlp 再试，或把字幕/文字稿贴给我。")
        log(f"[L2] 音频文件：{audio_files[0].name}，{audio_files[0].stat().st_size / 1024:.0f} KB")

        wav = cache_dir / "audio_16k.wav"
        if not wav.exists():
            ok, err = _run(
                ["ffmpeg", "-y", "-i", str(audio_files[0]), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                timeout=300,
            )
            if not ok:
                log(f"[L2] ffmpeg 转码失败：{err[-300:]}")
                return "", ("missing_dependency", "音频转码失败，请确认 ffmpeg 安装完整（winget install ffmpeg / brew install ffmpeg）。")

        import asr  # 延迟导入：asr 只在 L2 用到（scripts 目录已在搜索路径里）

        result = asr.transcribe_file(wav, offset_sec=start_sec or 0.0)
        if not result.get("ok"):
            return "", (result.get("error_reason") or "network", result.get("user_hint") or USER_HINTS["network"])
        segments = [Segment(start, end, text) for start, end, text in result["segments"]]
        transcript = format_transcript(segments)
        if result.get("partial"):
            return transcript, (result.get("error_reason") or "network", result.get("user_hint") or
                                "只转写了视频的一部分，后面的内容没拿到。可以指定其他时间段，或把文字稿贴给我。")
        if not transcript:
            return "", ("not_found", "转写没有得到文字，这个视频可能没有人声。")
        return transcript, None

    # -------------------------------------------------- L3

    def _l3_frames(
        self,
        url: str,
        cache_dir: Path,
        cached: dict[str, Any],
        start_sec: Optional[float],
        end_sec: Optional[float],
    ) -> dict[str, Any]:
        """下载够用的最低分辨率视频，场景检测抽关键帧。复用缓存的逐字稿。"""
        if not cached.get("content_text"):
            return failed_result(self.platform, url, "not_found", source_type="video",
                                 user_hint="没有可用逐字稿，无法解读全片。请提供关键画面的截图或文字稿。")

        def frame_result(keyframes: list[str], error_reason: str = "", user_hint: str = "") -> dict[str, Any]:
            # 帧是补充证据：既不能补齐缺失的语音，也不能抹掉原来的失败原因。
            method = cached["extraction_method"]
            return dict(cached, keyframes=keyframes, funnel_level="L3",
                        status="partial" if error_reason else cached["status"],
                        extraction_method="asr+frames" if keyframes and method == "asr" else method,
                        error_reason=cached["error_reason"] or error_reason,
                        user_hint=" ".join(filter(None, (cached["user_hint"], user_hint))))

        frames_dir = cache_dir / "frames"
        index_path = frames_dir / "index.json"
        index = _read_cached_json(index_path)
        if isinstance(index, dict) and 0 < len(index) <= FRAME_MAX_COUNT and all(
            re.fullmatch(r"frame_\d{5,}s\.jpg", name) and digest and digest == _frame_digest(frames_dir / name)
            for name, digest in index.items()
        ):
            log(f"[L3] 复用缓存关键帧 {len(index)} 张")
            return frame_result([str(frames_dir / name) for name in index])
        if not shutil.which("ffmpeg"):
            return frame_result(
                [], error_reason="missing_dependency",
                user_hint="抽帧需要 ffmpeg。Windows 可用 winget install ffmpeg，macOS 可用 brew install ffmpeg。逐字稿已拿到，可以先用文字部分。",
            )
        try:
            import yt_dlp  # 延迟导入
        except ImportError:
            return frame_result(
                [], error_reason="missing_dependency",
                user_hint="抽帧需要 yt-dlp（pip install yt-dlp）。逐字稿已拿到，可以先用文字部分。",
            )

        full_duration = cached["duration_sec"] or 0
        end = end_sec if end_sec is not None else full_duration
        duration = max(0, (min(end, full_duration) if full_duration else end) - (start_sec or 0))

        video_files = sorted(cache_dir.glob("video_src.*"))
        if not video_files:
            opts = _ytdlp_base_opts(cache_dir)
            opts.update(
                {
                    # 只要画面，≤480p 里最小的；没有就退到最低档
                    "format": "bestvideo[height<=480]/worstvideo/best[height<=480]/worst",
                    "outtmpl": str(cache_dir / "video_src.%(ext)s"),
                    "overwrites": True,
                }
            )
            if start_sec is not None or end_sec is not None:
                opts["download_ranges"] = yt_dlp.utils.download_range_func(
                    None, [(start_sec or 0.0, end_sec if end_sec is not None else float("inf"))]
                )
                opts["force_keyframes_at_cuts"] = True
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
            except Exception as exc:
                log(f"[L3] 视频下载失败：{str(exc)[:300]}")
                reason, hint = _classify_ytdlp_error(str(exc))
                return frame_result([], error_reason=reason, user_hint=hint)
            video_files = sorted(cache_dir.glob("video_src.*"))
            if not video_files:
                return frame_result(
                    [], error_reason="network",
                    user_hint="视频画面下载失败。逐字稿已拿到，可以先用文字部分，或截几张关键画面发给我。",
                )
        video_path = video_files[0]
        log(f"[L3] 视频文件：{video_path.name}，{video_path.stat().st_size / 1024 / 1024:.1f} MB")
        return self._run_l3_on_file(video_path, cache_dir, cached, start_sec, end_sec)

    def _run_l3_on_file(
        self,
        video_path: Path,
        cache_dir: Path,
        cached: dict[str, Any],
        start_sec: Optional[float],
        end_sec: Optional[float],
    ) -> dict[str, Any]:
        """对已有本地视频文件执行场景/软切抽帧并写入带完整性校验的索引。"""
        def frame_result(keyframes: list[str], error_reason: str = "", user_hint: str = "") -> dict[str, Any]:
            method = cached["extraction_method"]
            return dict(
                cached,
                keyframes=keyframes,
                funnel_level="L3",
                status="partial" if error_reason else cached["status"],
                extraction_method="asr+frames" if keyframes and method == "asr" else method,
                error_reason=cached["error_reason"] or error_reason,
                user_hint=" ".join(filter(None, (cached["user_hint"], user_hint))),
            )

        frames_dir = cache_dir / "frames"
        index_path = frames_dir / "index.json"
        index = _read_cached_json(index_path)
        if isinstance(index, dict) and 0 < len(index) <= FRAME_MAX_COUNT and all(
            re.fullmatch(r"frame_\d{5,}s\.jpg", name) and digest and digest == _frame_digest(frames_dir / name)
            for name, digest in index.items()
        ):
            log(f"[L3] 复用缓存关键帧 {len(index)} 张")
            return frame_result([str(frames_dir / name) for name in index])

        full_duration = cached["duration_sec"] or 0
        end = end_sec if end_sec is not None else full_duration
        duration = max(0, (min(end, full_duration) if full_duration else end) - (start_sec or 0))

        try:
            frames_dir.mkdir(parents=True, exist_ok=True)
            # 同一请求可能并发或在 ffmpeg 中断后重试；只接收本次私有目录里的输出。
            with tempfile.TemporaryDirectory(dir=frames_dir) as tmp:
                work = Path(tmp)
                kept = self._scene_frames(video_path, work, duration)
                if not kept:
                    log("[L3] 没有可定位的场景帧，改用均匀间隔兜底")
                    kept = self._fallback_frames(video_path, work, duration, start_sec)
                if not kept:
                    return frame_result([], "missing_dependency", "抽帧没有成功，请确认 ffmpeg 安装完整。逐字稿已拿到，可以先用文字部分。")

                index = {}
                for path, t in kept:
                    digest = _frame_digest(path)
                    if not digest:
                        return frame_result([], "network", "关键帧文件不完整。请提供关键画面的截图，或先用已取得的文字。")
                    name = f"frame_{round((start_sec or 0.0) + t):05d}s.jpg"
                    path.replace(frames_dir / name)
                    index[name] = digest
                # 先发布完整图片，最后一次替换索引；读者不会看到半写的 JSON。
                staged = work / "index.json"
                staged.write_text(json.dumps(index), encoding="utf-8")
                staged.replace(index_path)
        except OSError as exc:
            log(f"[L3] 关键帧未能保存：{type(exc).__name__}")
            return frame_result([], "network", "关键帧未能完整保存。可以先用已取得的文字，或提供关键画面的截图。")
        log(f"[L3] 关键帧 {len(index)} 张")
        return frame_result([str(frames_dir / name) for name in index])

    def _scene_frames(self, video_path: Path, work: Path, duration: float) -> list[tuple[Path, float]]:
        """只接受本轮有时间依据的硬切帧；不可定位时尝试按软切换时间重新取帧。"""
        scale = f"scale=w='min(iw,{FRAME_LONG_SIDE})':h='min(ih,{FRAME_LONG_SIDE})':force_original_aspect_ratio=decrease"
        ok, err = _run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo,{scale}",
                "-fps_mode", "vfr", "-q:v", "5",
                str(work / "scene_%04d.jpg"),
            ],
            timeout=600,
        )
        if ok:
            try:
                times = [float(t) for t in re.findall(r"\bpts_time:(\S+)", err)]
            except ValueError:  # showinfo 也可能报告 N/A；不能猜它是哪一秒。
                times = []
            files = set(work.glob("scene_*.jpg"))
            pairs = [(work / f"scene_{i:04d}.jpg", t) for i, t in enumerate(times, 1)]
            if pairs and len(files) == len(pairs) and all(p in files and math.isfinite(t) and t >= 0 for p, t in pairs):
                return _select_frames(pairs, duration)

        # 硬切失败时可能留下本轮的残帧，定点重抽要用另一个输出名。
        times = _soft_scene_times(_scan_scene_scores(video_path), duration)
        candidates = [(work / f"soft_{i:04d}.jpg", t) for i, t in enumerate(times)]
        kept = []
        for path, t in _select_frames(candidates, duration):
            ok, _ = _run(
                ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
                 "-frames:v", "1", "-vf", scale, "-q:v", "5", str(path)],
                timeout=60,
            )
            if ok and path.is_file():
                kept.append((path, t))
        return kept

    def _fallback_frames(
        self, video_path: Path, frames_dir: Path, duration: float, start_sec: Optional[float]
    ) -> list[tuple[Path, float]]:
        """场景检测一张都没抽到时（画面几乎静止），按均匀间隔补抽最多 8 张。"""
        count = min(8, max(1, int(duration // 60) or 1))
        step = (duration or count) / count
        scale = f"scale=w='min(iw,{FRAME_LONG_SIDE})':h='min(ih,{FRAME_LONG_SIDE})':force_original_aspect_ratio=decrease"
        kept: list[tuple[Path, float]] = []
        for i in range(count):
            t = step * i + step / 2
            target = frames_dir / f"fallback_{i:02d}.jpg"
            ok, _ = _run(
                ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
                 "-frames:v", "1", "-vf", scale, "-q:v", "5", str(target)],
                timeout=60,
            )
            if ok and target.exists():
                kept.append((target, t))
        return kept

    # -------------------------------------------------- 逐字稿缓存

    def _save_transcript(self, cache_dir: Path, url: str, start: Optional[float], end: Optional[float],
                         result: dict[str, Any]) -> None:
        # 请求 URL 可能与元数据中的平台规范 URL 不同；两者不能互相替代。
        data = {"request": [url, start, end], "result": result, "saved_at": time.time()}
        data["sha256"] = _transcript_digest(data)
        try:
            # 临时文件与目标同卷；写入/关闭成功后才替换，锁冲突或磁盘满不动旧记录。
            with tempfile.TemporaryDirectory(dir=cache_dir) as tmp:
                staged = Path(tmp) / "transcript.json"
                staged.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                staged.replace(cache_dir / "transcript.json")
        except OSError as exc:
            log(f"[缓存] 逐字稿未能写入，下次可能需要重新取材：{type(exc).__name__}")

    def _load_transcript(self, cache_dir: Path, url: str, start: Optional[float], end: Optional[float]
                         ) -> Optional[dict[str, Any]]:
        data = _read_cached_json(cache_dir / "transcript.json")
        if not isinstance(data, dict) or data.get("request") != [url, start, end]:
            return None
        # 拷贝或重存不能给旧内容续期。旧格式没有摘要，不能证明正文未被改写。
        saved_at = data.get("saved_at")
        if type(saved_at) not in (int, float) or not time.time() - CACHE_TTL_SEC <= saved_at <= time.time():
            return None
        digest = data.pop("sha256", None)
        if not isinstance(digest, str) or digest != _transcript_digest(data):
            return None
        # Python 中 False == 0，不能让 JSON 布尔值冒充请求的时间。
        if any(t is not None and type(t) not in (float, int) for t in data["request"][1:]):
            return None
        r = data.get("result")
        fields = make_result("success")
        if not isinstance(r, dict) or r.keys() != fields.keys():
            return None
        if any(not isinstance(r[k], str) for k, v in fields.items() if isinstance(v, str)):
            return None
        if (r["source_type"] != "video" or r["platform"] != self.platform or not r["url"].strip()
                or not r["content_text"].strip() or r["keyframes"] != [] or r["needs_confirmation"]):
            return None
        duration = r["duration_sec"]
        if duration is not None and (type(duration) not in (float, int) or not 0 <= duration < math.inf):
            return None
        if (r["extraction_method"], r["funnel_level"]) not in (("asr", "L2"), ("subtitle", "L1")):
            return None
        if r["status"] == "success" and not r["error_reason"] and not r["user_hint"]:
            return r
        if (r["status"] == "partial" and r["extraction_method"] == "asr" and r["user_hint"].strip()
                and r["error_reason"] in ("network", "anti_scrape", "login_wall", "missing_dependency", "not_found", "asr_not_configured")):
            return r
        return None


def _scan_scene_scores(video_path: Path, timeout: float = 600) -> list[tuple[float, float]]:
    """低阈值全量扫描，返回 (时间, 场景分数) 序列。

    用 metadata=print 读 select 过滤器写入的分数（ffmpeg 8 不再把分数
    打进 showinfo 行）。失败或超时返回空列表，由调用方走兜底。
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-loglevel", "info", "-i", str(video_path),
                "-vf", f"select='gt(scene,{SCENE_SCAN_FLOOR})',metadata=print:file=-",
                "-fps_mode", "vfr", "-f", "null", "-",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    pairs = re.findall(r"pts_time:([0-9.]+)\s*\nlavfi\.scene_score=([0-9.]+)", proc.stdout or "")
    return [(float(t), float(s)) for t, s in pairs]


def _soft_scene_times(scores: list[tuple[float, float]], duration: Optional[float] = None) -> list[float]:
    """从 (时间, 分数) 序列里挑“变化后静止”的软切换帧（幻灯片换页一类）。

    换页是软过渡：分数只有 0.02–0.1，但换完画面会静止几秒；
    手持拍摄是持续运动，任何一帧后面 2 秒内都还有运动帧，全部被拒绝，
    从而不会把运动噪声当成换页。取每段过渡的末帧（画面已完整呈现）。
    视频结尾处要还剩至少 2 秒片长才算“观察到静止”，否则持续运动的
    最后一帧会被误判成换页。
    """
    times: list[float] = []
    n = len(scores)
    for i, (t, s) in enumerate(scores):
        if s < SCENE_SCAN_FLOOR:
            continue
        if i + 1 < n:
            # 下一个变化点还在 2 秒内：说明仍在过渡中，不是末帧
            if scores[i + 1][0] - t <= SCENE_QUIET_SEC:
                continue
            times.append(t)
        elif duration is None or duration - t >= SCENE_QUIET_SEC:
            # 扫描到视频末尾：片长还剩够久才算“观察到静止”，
            # 否则持续运动的最后一帧会被误判成换页
            times.append(t)
    return times


def _select_frames(pairs: list[tuple[Path, float]], duration: float) -> list[tuple[Path, float]]:
    """最小间隔 ≥3 秒去重，再压到上限帧数（均匀抽稀，保留首尾）。"""
    spaced: list[tuple[Path, float]] = []
    last_t = -float("inf")
    for path, t in pairs:
        if t < 0 or not math.isfinite(t):
            continue
        if t - last_t >= FRAME_MIN_GAP:
            spaced.append((path, t))
            last_t = t
    if len(spaced) <= FRAME_MAX_COUNT:
        return spaced
    step = (len(spaced) - 1) / (FRAME_MAX_COUNT - 1)
    return [spaced[round(i * step)] for i in range(FRAME_MAX_COUNT)]