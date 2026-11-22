#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""清理视频抓取的临时缓存（字幕、逐字稿、音频、关键帧）。

用法：
    python scripts/cleanup_cache.py          # 清理超过 24 小时未使用的缓存
    python scripts/cleanup_cache.py --all    # 全部清理

缓存位于系统临时目录下的 plain-talk-decoder/。只删这个目录里的内容，
不碰其他任何文件。标准输出只打印一份 UTF-8 JSON，退出码恒为 0。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

MAX_AGE_HOURS = 24


def cache_root() -> Path:
    return Path(tempfile.gettempdir()) / "plain-talk-decoder"


def _last_used(entry: Path) -> float:
    """目录及其内容的最近修改时间，作为“最后使用”的近似。"""
    latest = entry.stat().st_mtime
    for path in entry.rglob("*"):
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest


def cleanup(all_: bool = False, max_age_hours: float = MAX_AGE_HOURS, root: Optional[Path] = None) -> dict[str, Any]:
    """执行清理，返回统计结果。root 参数仅供测试注入临时目录。"""
    root = root if root is not None else cache_root()
    removed = 0
    freed = 0
    kept = 0
    if root.exists():
        cutoff = time.time() - max_age_hours * 3600
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                if not all_ and _last_used(entry) >= cutoff:
                    kept += 1
                    continue
                size = sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())
                shutil.rmtree(entry)
            except OSError:
                kept += 1
                continue
            removed += 1
            freed += size
    return {
        "status": "success",
        "removed": removed,
        "kept": kept,
        "freed_bytes": freed,
        "user_hint": (
            f"已清理 {removed} 份视频缓存，释放约 {freed / 1024 / 1024:.1f} MB。"
            if removed else "没有需要清理的视频缓存。"
        ),
    }


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="清理人话解码器的视频临时缓存")
    parser.add_argument("--all", action="store_true", help="全部清理（默认只清超过 24 小时的）")
    args = parser.parse_args(argv)
    try:
        result = cleanup(all_=args.all)
    except Exception as exc:  # 清理失败也要输出合法 JSON
        print(f"[cleanup] 意外错误：{type(exc).__name__}", file=sys.stderr)
        result = {
            "status": "failed",
            "removed": 0,
            "kept": 0,
            "freed_bytes": 0,
            "user_hint": "缓存清理没有完成，不影响使用；系统也会定期清理临时目录。",
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())