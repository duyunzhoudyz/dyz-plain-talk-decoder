#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""环境自检：报告已具备与缺失的能力，并给出安装指引。

用法：
    python scripts/check_env.py

即使缺少全部可选依赖也能运行（只用标准库）。标准输出只打印一份 UTF-8 JSON，
日志走标准错误；只要输出了 JSON 就以退出码 0 结束。
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# 也支持 python -I scripts/check_env.py；提供方与协议判断复用转写入口。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asr

MIN_PYTHON = (3, 10)
# yt-dlp 版本发布很频繁，太旧是视频抓取失败最常见的原因。按版本号里的日期判断。
YTDLP_STALE_DAYS = 120


def check_module(name: str) -> Optional[str]:
    """尝试导入模块，成功返回版本号（拿不到则返回 'unknown'），失败返回 None。"""
    try:
        mod = importlib.import_module(name)
    except Exception:
        return None
    return str(getattr(mod, "__version__", "unknown"))


def check_python() -> dict[str, Any]:
    ok = sys.version_info >= MIN_PYTHON
    return {
        "name": "python",
        "ok": ok,
        "version": ".".join(map(str, sys.version_info[:3])),
        "hint": "" if ok else "需要 Python 3.10 或更新版本。请到 python.org 下载安装。",
    }


def check_core() -> list[dict[str, Any]]:
    """图文抓取所需的核心依赖。"""
    items = []
    for label, module, pip_name in (
        ("requests", "requests", "requests"),
        ("beautifulsoup4", "bs4", "beautifulsoup4"),
        ("readability-lxml", "readability", "readability-lxml"),
    ):
        version = check_module(module)
        items.append(
            {
                "name": label,
                "ok": version is not None,
                "version": version or "",
                "hint": "" if version else f"运行：pip install {pip_name}（或 pip install -r requirements.txt）",
            }
        )
    return items


def _ytdlp_age_hint(version: str) -> str:
    """yt-dlp 版本形如 2024.08.06；超过阈值就提示升级。"""
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", version)
    if not m:
        return ""
    from datetime import date

    try:
        released = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""
    age = (date.today() - released).days
    if age > YTDLP_STALE_DAYS:
        return f"yt-dlp 已有约 {age} 天没更新，视频抓取失败时请先升级：pip install -U yt-dlp"
    return ""


def check_optional() -> list[dict[str, Any]]:
    """视频与 ASR 的可选能力。缺失不影响文字与网页解码。"""
    items: list[dict[str, Any]] = []

    ytdlp = check_module("yt_dlp")
    if ytdlp == "unknown":
        # yt_dlp 顶层没有 __version__，版本号在 yt_dlp.version 子模块里
        ytdlp = check_module("yt_dlp.version") or ytdlp
    cli_note = ""
    if ytdlp is None and shutil.which("yt-dlp"):
        # 只有单独的命令行程序不够：视频脚本通过 import yt_dlp 使用 Python 库
        cli_note = "检测到 yt-dlp 命令行程序，但本技能的脚本需要 Python 库。"
    stale = _ytdlp_age_hint(ytdlp) if ytdlp else ""
    items.append(
        {
            "name": "yt-dlp",
            "ok": ytdlp is not None,
            "version": ytdlp or "",
            "hint": ("" if ytdlp else cli_note + "视频功能需要：pip install yt-dlp") or stale,
            "needed_for": "视频字幕与音频下载（Phase 3 启用）",
        }
    )

    ffmpeg = shutil.which("ffmpeg")
    items.append(
        {
            "name": "ffmpeg",
            "ok": ffmpeg is not None,
            "version": "",
            "hint": "" if ffmpeg else "视频转写与抽帧需要 ffmpeg。Windows 可用 winget install ffmpeg，macOS 可用 brew install ffmpeg。",
            "needed_for": "音频处理与抽帧（Phase 3 启用）",
        }
    )

    for label, module, pip_name, purpose in (
        ("faster-whisper", "faster_whisper", "faster-whisper", "本地语音转文字（ASR）"),
        ("openai", "openai", "openai", "云端语音转文字（ASR）客户端"),
    ):
        version = check_module(module)
        items.append(
            {
                "name": label,
                "ok": version is not None,
                "version": version or "",
                "hint": "" if version else f"需要时运行：pip install {pip_name}",
                "needed_for": purpose,
            }
        )
    return items


def check_asr_config() -> dict[str, Any]:
    """ASR 是否配置。只报告“有没有设置”，**绝不输出密钥内容**。"""
    provider, problem = asr.get_provider()
    if not provider:
        problem = "未配置语音转文字。没有字幕的视频需要它；也可以直接把文字稿贴给我。配置方法见 README。"
    # qwen-audio 原生协议只用标准库；qwen3-asr 兼容协议与其他云端接口需 openai。
    client = ""
    if provider == "local_whisper":
        client = "faster-whisper"
    elif provider == "openai_compatible" and not asr.is_native_dialect(os.environ.get("ASR_MODEL", "")):
        client = "openai"
    config = {
        "name": "asr_config", "ok": not problem, "hint": problem,
        "provider": provider if provider in asr.PROVIDERS else "", "required_client": client,
        "check_scope": (
            "video_transcribe 只表示本机配置与依赖预检通过；视频转写按 ffmpeg 生成的 PCM WAV 帧数读取时长，切片不依赖 ffprobe。"
            "未验证云端鉴权、额度、服务或本地模型实际推理。"
        ),
    }
    if provider == "openai_compatible":
        config["api_key_set"] = not problem
    return config


def summarize(core: list[dict], optional: list[dict], python: dict, asr_config: dict) -> dict[str, Any]:
    can_fetch_articles = python["ok"] and all(i["ok"] for i in core)
    ytdlp_ok = any(i["name"] == "yt-dlp" and i["ok"] for i in optional)
    ffmpeg_ok = any(i["name"] == "ffmpeg" and i["ok"] for i in optional)
    client = asr_config["required_client"]
    client_ok = not client or any(i["name"] == client and i["ok"] for i in optional)
    return {
        "paste_text": True,  # 粘贴文字永远可用，不需要任何安装
        "fetch_articles": can_fetch_articles,
        "video_subtitles": can_fetch_articles and ytdlp_ok,
        "video_transcribe": python["ok"] and ytdlp_ok and ffmpeg_ok and asr_config["ok"] and client_ok,
    }


def run_checks() -> dict[str, Any]:
    python = check_python()
    core = check_core()
    optional = check_optional()
    asr = check_asr_config()
    capabilities = summarize(core, optional, python, asr)

    todo = [i["hint"] for i in [python, *core] if not i["ok"] and i["hint"]]
    todo += [i["hint"] for i in optional if i["hint"] and
             (i["name"] not in ("openai", "faster-whisper") or i["name"] == asr["required_client"])]
    if asr["hint"]:
        todo.append(asr["hint"])

    return {
        "python": python,
        "core": core,
        "optional": optional,
        "asr": asr,
        "capabilities": capabilities,
        "user_hint": (
            "文字和截图可以直接用，不需要安装任何东西。" if not todo
            else "文字和截图可以直接用。想让我自动读取链接或视频，还需要：" + "；".join(todo)
        ),
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        result = run_checks()
    except Exception as exc:  # 自检本身不能崩
        print(f"[check_env] 意外错误：{type(exc).__name__}", file=sys.stderr)
        result = {
            "capabilities": {"paste_text": True},
            "user_hint": "环境检查没能完成，但文字和截图可以直接用。",
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
