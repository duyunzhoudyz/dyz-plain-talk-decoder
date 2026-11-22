"""render_card.py 单页 HTML 分享卡片单元测试（零外部依赖，离线）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "dyz-plain-talk-decoder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_card as rc  # noqa: E402

SAMPLE_MD = """## 一句话讲人话
> 这份宣传材料把最好年份当常态宣传，建议先别急着下单。

## 事实与拆解
- **对方重点让你看的**：近三年最高年化 6.8%，赠送体检卡。
- **对方没有提到或一笔带过的**：管理费、退保损失与全部历史年份收益。

## 核查与可信度
- 最高年化 6.8%：`[存疑/有出入]`（[官网披露](https://example.com/report) 显示为单年峰值）
- 行业排名第一：`[未证实/单方说法]`（未找到公开榜单）
- 官方备案编号：`[已核实]`（[监管页](https://example.com/reg) 一致）
"""


class TestRenderCard:
    def test_renders_self_contained_html_with_badges_and_cautions(self):
        html_doc, stats = rc.render_card_html(SAMPLE_MD, title="保险宣传解码", source="示例页面")
        assert "<!doctype html>" in html_doc.lower()
        assert "badge-ok" in html_doc
        assert "badge-warn" in html_doc
        assert "badge-unverified" in html_doc
        assert "只看存疑与避坑" in html_doc
        assert "https://example.com/report" in html_doc
        # 不依赖任何外部 CDN
        assert "cdn." not in html_doc and "googleapis.com" not in html_doc
        assert stats["sections"] == 3
        assert stats["caution_items"] >= 3

    def test_escapes_script_injection_and_blocks_javascript_links(self):
        evil = (
            "## 一句话讲人话\n"
            "测试 <script>alert(1)</script> 和 [点我](javascript:alert(2)) 以及 [合法](https://example.com/ok)。"
        )
        html_doc, _ = rc.render_card_html(evil, title="<img src=x onerror=alert(1)>")
        assert "<script>alert(1)</script>" not in html_doc
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_doc
        assert "javascript:alert(2)" not in html_doc
        assert 'href="https://example.com/ok"' in html_doc
        assert "<img src=x" not in html_doc

    def test_cli_generates_file_and_exits_zero(self, tmp_path):
        src = tmp_path / "in.md"
        out = tmp_path / "out" / "card.html"
        src.write_text(SAMPLE_MD, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_card.py"),
                "--file", str(src),
                "--output", str(out),
                "--title", "测试卡片",
                "--source", "单元测试",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["status"] == "success"
        assert out.is_file()
        assert "测试卡片" in out.read_text(encoding="utf-8")

    def test_cli_missing_input_file_fails_honestly_with_exit_zero(self, tmp_path):
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_card.py"),
                "--file", str(tmp_path / "nope.md"),
                "--output", str(tmp_path / "out.html"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["status"] == "failed"
