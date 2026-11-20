"""缓存清理的离线测试：只操作注入的临时目录，不碰真实缓存。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "dyz-plain-talk-decoder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cleanup_cache as cc  # noqa: E402


def make_entry(root: Path, name: str, age_hours: float) -> Path:
    entry = root / name
    (entry / "frames").mkdir(parents=True)
    f = entry / "frames" / "frame_00001s.jpg"
    f.write_bytes(b"x" * 1024)
    old = time.time() - age_hours * 3600
    os.utime(f, (old, old))
    os.utime(entry / "frames", (old, old))
    os.utime(entry, (old, old))
    return entry


class TestCleanup:
    def test_old_removed_new_kept(self, tmp_path):
        root = tmp_path / "cache"
        make_entry(root, "old", 30)
        make_entry(root, "new", 1)
        r = cc.cleanup(root=root)
        assert r["status"] == "success"
        assert r["removed"] == 1 and r["kept"] == 1
        assert r["freed_bytes"] >= 1024
        assert not (root / "old").exists()
        assert (root / "new").exists()

    def test_all_removes_everything(self, tmp_path):
        root = tmp_path / "cache"
        make_entry(root, "old", 30)
        make_entry(root, "new", 1)
        r = cc.cleanup(all_=True, root=root)
        assert r["removed"] == 2 and r["kept"] == 0
        assert list(root.iterdir()) == []

    def test_missing_root_is_fine(self, tmp_path):
        r = cc.cleanup(root=tmp_path / "nope")
        assert r["status"] == "success" and r["removed"] == 0

    def test_recently_used_subfile_keeps_entry(self, tmp_path):
        root = tmp_path / "cache"
        entry = make_entry(root, "mixed", 30)
        # 目录很旧但里面有个刚用过的文件：按最近使用算，应保留
        fresh = entry / "transcript.json"
        fresh.write_text("{}", encoding="utf-8")
        r = cc.cleanup(root=root)
        assert r["kept"] == 1 and (entry).exists()

    def test_cli_outputs_json_exit_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cc, "cache_root", lambda: tmp_path / "cache")
        code = cc.main([])
        assert code == 0
        r = json.loads(capsys.readouterr().out)
        assert r["status"] == "success"