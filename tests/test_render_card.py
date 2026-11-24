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

    def test_inline_code_and_rich_markdown_blocks(self):
        """行内代码不被粗体破坏；表格、代码块、分割线、多行引用、嵌套列表正常渲染（P1-7）。"""
        md = (
            "## 一句话讲人话\n"
            "> 第一行结论\n"
            "> 第二行补充\n\n"
            "---\n\n"
            "测试行内 `code **not-bold**` 与 [**粗体链接**](https://example.com/a)。\n\n"
            "| 项 | 数值 |\n"
            "| :--- | :--- |\n"
            "| 内存 | 95MB |\n\n"
            "- 外层条目\n"
            "  - 内层子条目\n\n"
            "```python\n"
            "# 这是代码注释，不应被切成标题\n"
            "x = 1\n"
            "y = 2\n"
            "```\n"
        )
        doc, stats = rc.render_card_html(md, title="富文本测试")
        assert "<code>code **not-bold**</code>" in doc
        assert 'href="https://example.com/a"' in doc and "<strong>粗体链接</strong>" in doc
        assert "<table><thead><tr><th>项</th><th>数值</th></tr></thead>" in doc
        assert '<hr class="divider" />' in doc
        assert "<pre " in doc and "# 这是代码注释，不应被切成标题\nx = 1\ny = 2" in doc
        assert stats["sections"] == 1
        assert doc.count("<blockquote") == 1
        assert doc.count("<ul>") >= 2

    def test_clean_example_not_misflagged_as_caution(self):
        """干净样本里的“没有明显营销水分”和“未联网核查”不得误标为避坑项（P1-8）。"""
        raw = (ROOT / "examples" / "05-clean-article-no-nitpick.md").read_text(encoding="utf-8")
        body = raw.split("\n---\n", 1)[1].strip()
        _, stats = rc.render_card_html(body, title="干净样本")
        assert stats["caution_items"] == 0

    def test_plain_paragraph_report_preserves_lead_in_filter_mode(self):
        """无 H2 标题的自然段报告，首段应带 lead-item，避免筛选时被隐藏（P1-8）。"""
        md = "首段直接给出结论与分界。\n\n第二段指出没有提到的关键盲区。"
        doc, stats = rc.render_card_html(md, title="自然段样本")
        assert "lead-item" in doc
        assert stats["caution_items"] == 1

    def test_cli_stdin_utf8_chinese_works(self, tmp_path):
        out = tmp_path / "stdin_card.html"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "render_card.py"), "--output", str(out), "--title", "管道中文测试"],
            input="## 一句话讲人话\n> 管道传入中文正文正常渲染。",
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["status"] == "success"
        assert "管道传入中文正文正常渲染" in out.read_text(encoding="utf-8")

    def test_profile_free_share_copy_and_cli_warning(self, tmp_path):
        fact = "次卡须提前预约。"
        # 模拟宿主先给出个性化原答，收到分享请求后另写副本；原答及个性化元信息不进脚本。
        private_markers = ("星砂预算3000元", "星砂只在周末有空", "星砂工作单位", "星砂家庭安排")
        private_report = (
            f"{fact}\n\n按你说的{private_markers[0]}，先确认能约到几场。",
            f"次卡解码（{private_markers[1]}）", f"场馆价目表（靠近{private_markers[2]}）", f"附注：{private_markers[3]}",
        )
        share_copy = f"{fact}\n\n付款前先确认能约到几场。"
        out = tmp_path / "share.html"
        cmd = [sys.executable, "-B", str(SCRIPTS / "render_card.py")]
        proc = subprocess.run(
            [*cmd, "--output", str(out), "--title", "次卡解码", "--source", "场馆价目表", "--meta", "离线核对"],
            input=share_copy, capture_output=True, encoding="utf-8", check=True,
        )
        assert json.loads(proc.stdout)["status"] == "success"
        doc = out.read_text(encoding="utf-8")
        assert all(marker in private and marker not in doc for marker, private in zip(private_markers, private_report))
        assert all(public in doc for public in (fact, "次卡解码", "场馆价目表", "离线核对"))

        help_text = subprocess.run([*cmd, "--help"], capture_output=True, encoding="utf-8", check=True).stdout
        assert "宿主内单独生成并核对过的无画像分享副本" in help_text
        assert help_text.count("禁止传入真实用户画像或个人前提") >= 5
        assert "HTML 转义不是脱敏" in help_text
        assert "参数不完整" not in help_text
