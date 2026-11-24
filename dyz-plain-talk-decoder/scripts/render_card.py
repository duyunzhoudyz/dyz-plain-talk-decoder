#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""可选单页 HTML 分享卡片生成器（纯标准库，零外部依赖）。

用法：
    python scripts/render_card.py --file report.md --output card.html [--title "标题"] [--source "来源"]
    python scripts/render_card.py --output card.html < report.md

特点：
- 输出完全自包含的单文件 HTML（无外部 CDN、无外链脚本、离线可打开、适合发给朋友或打印导出 PDF）；
- 移动端与桌面端自适应，支持浅色/深色主题自动适配；
- 内置“只看存疑与避坑”一键筛选按钮与“打印 / 存 PDF”按钮；
- 严格 HTML 转义所有用户文本与仅放行 http(s) 链接，防止抓取正文夹带脚本造成 XSS。

约定（AGENTS.md 第 7 节）：
- 标准输出只打印一份 UTF-8 JSON，退出码恒为 0；成败由 `status` 字段表达。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

# 用于标注卡片中哪些条目属于“存疑/避坑/缺失/风险”以便一键筛选
# 不把单纯的“未联网核查”当作避坑项，避免把扎实干净的离线报告误标成避坑。
CAUTION_KEYWORDS = (
    "[存疑/有出入]",
    "[未证实/单方说法]",
    "没有提到",
    "一笔带过",
    "盲区",
    "未写清",
    "未说明",
    "缺证据",
    "与记录冲突",
    "风险",
    "先别急",
    "不建议",
    "不可退",
    "不顺延",
    "水分",
    "漏洞",
    "不是法律意见",
    "持牌",
    "缺口",
    "缺失",
)

# 命中否定语境时不按对应负面词触发避坑，防止“没有明显营销水分”“没有矛盾”被误标
NEGATED_CAUTION_RE = re.compile(
    r"(没有明显|无明显|没有|并无|不存在)(?:营销|逻辑)?(?:水分|漏洞|风险|冲突|盲区|缺口)"
)

CAUTION_HEADING_KEYWORDS = ("盲区", "漏洞", "风险", "存疑", "待核实")

BADGE_REPLACEMENTS = [
    ("[已核实]", '<span class="badge badge-ok">已核实</span>'),
    ("[存疑/有出入]", '<span class="badge badge-warn">存疑/有出入</span>'),
    ("[未证实/单方说法]", '<span class="badge badge-unverified">未证实/单方说法</span>'),
]


def _safe_href(url: str) -> str:
    cleaned = url.strip()
    if re.match(r"^https?://", cleaned, re.I):
        return html.escape(cleaned, quote=True)
    return ""


def _render_inline(text: str) -> str:
    """渲染安全行内 Markdown（行内代码、链接、粗体、三态徽章），全部先转义 HTML。"""
    tokens: list[str] = []

    def stash(snippet: str) -> str:
        idx = len(tokens)
        tokens.append(snippet)
        return f"\x00TOK{idx}\x00"

    # 1. 先提取行内代码并立即 stash，防止其中的 **...** 被粗体正则二次改写
    def repl_code(m: re.Match[str]) -> str:
        raw_code = m.group(1)
        for raw_tag, badge_html in BADGE_REPLACEMENTS:
            if raw_code == raw_tag:
                return stash(badge_html)
        return stash(f"<code>{html.escape(raw_code)}</code>")

    work = re.sub(r"`([^`]+)`", repl_code, text)

    # 2. 提取 Markdown 链接，确保只放行 http(s)，链接文字支持粗体
    def repl_link(m: re.Match[str]) -> str:
        label = re.sub(r"\*\*([^*]+)\*\*", lambda bm: f"<strong>{bm.group(1)}</strong>", html.escape(m.group(1)))
        href = _safe_href(m.group(2))
        if not href:
            return stash(label)
        return stash(f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>')

    work = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_link, work)
    work = html.escape(work)

    # 3. 粗体与未包反引号的三态徽章
    work = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", work)
    for raw_tag, badge_html in BADGE_REPLACEMENTS:
        work = work.replace(html.escape(raw_tag), badge_html)

    for idx, snippet in enumerate(tokens):
        work = work.replace(f"\x00TOK{idx}\x00", snippet)
    return work


def _is_caution(text: str) -> bool:
    scrubbed = NEGATED_CAUTION_RE.sub("", text or "")
    return any(k in scrubbed for k in CAUTION_KEYWORDS)


def _heading_is_caution(heading: str) -> bool:
    return any(k in (heading or "") for k in CAUTION_HEADING_KEYWORDS)


def _is_table_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", line.strip()))


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _parse_sections(markdown_text: str) -> list[dict[str, Any]]:
    """按 H2/H3 切分报告；跳过围栏代码块里的 # 注释；没有标题的纯自然段归入单节。"""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"heading": "", "lines": []}
    in_fence = False
    for raw_line in markdown_text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            current["lines"].append(raw_line)
            continue
        if not in_fence:
            m = re.match(r"^#{1,3}\s+(.+)$", stripped)
            if m:
                if current["heading"] or any(line.strip() for line in current["lines"]):
                    sections.append(current)
                current = {"heading": m.group(1).strip(), "lines": []}
                continue
        current["lines"].append(raw_line)
    if current["heading"] or any(line.strip() for line in current["lines"]):
        sections.append(current)
    return sections


def _render_blocks(lines: list[str], section_heading: str, *, mark_first_lead: bool = False) -> tuple[str, int]:
    """把一节内的段落、引用、列表（含嵌套）、表格、分割线和代码块渲染成 HTML。"""
    parts: list[str] = []
    caution_count = 0
    heading_caution = _heading_is_caution(section_heading)
    list_stack: list[tuple[str, int]] = []  # [(tag, indent), ...]
    para_buf: list[str] = []
    quote_buf: list[str] = []
    lead_marked = False

    def close_lists(target_indent: int = -1) -> None:
        while list_stack and list_stack[-1][1] > target_indent:
            tag, _ = list_stack.pop()
            parts.append(f"</{tag}>")

    def flush_quote() -> None:
        nonlocal caution_count, lead_marked
        if not quote_buf:
            return
        raw = " ".join(quote_buf)
        quote_buf.clear()
        caution = _is_caution(raw) or heading_caution
        if caution:
            caution_count += 1
        extra = " lead-item" if (mark_first_lead and not lead_marked) else ""
        if extra:
            lead_marked = True
        parts.append(
            f'<blockquote class="lead item{extra}" data-caution="{str(caution).lower()}">'
            f"{_render_inline(raw)}</blockquote>"
        )

    def flush_para() -> None:
        nonlocal caution_count, lead_marked
        if not para_buf:
            return
        raw = " ".join(s.strip() for s in para_buf)
        para_buf.clear()
        caution = _is_caution(raw) or heading_caution
        if caution:
            caution_count += 1
        extra = " lead-item" if (mark_first_lead and not lead_marked) else ""
        if extra:
            lead_marked = True
        cls = f"item caution{extra}" if caution else f"item{extra}"
        parts.append(f'<p class="{cls}" data-caution="{str(caution).lower()}">{_render_inline(raw)}</p>')

    i = 0
    total = len(lines)
    while i < total:
        raw_line = lines[i]
        s = raw_line.strip()

        # 围栏代码块
        if s.startswith("```"):
            flush_para()
            flush_quote()
            close_lists()
            code_lines: list[str] = []
            i += 1
            while i < total and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            code_html = html.escape("\n".join(code_lines))
            parts.append(f'<pre class="item" data-caution="false"><code>{code_html}</code></pre>')
            i += 1
            continue

        if not s:
            flush_para()
            flush_quote()
            close_lists()
            i += 1
            continue

        # 水平分割线
        if re.fullmatch(r"([-*_])\1{2,}", s):
            flush_para()
            flush_quote()
            close_lists()
            parts.append('<hr class="divider" />')
            i += 1
            continue

        # Markdown 表格（表头 + 分隔线）
        if "|" in s and i + 1 < total and _is_table_sep(lines[i + 1]):
            flush_para()
            flush_quote()
            close_lists()
            headers = _split_table_row(s)
            i += 2
            rows: list[list[str]] = []
            while i < total and "|" in lines[i].strip() and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            table_text = " ".join(headers + [c for r in rows for c in r])
            caution = _is_caution(table_text) or heading_caution
            if caution:
                caution_count += 1
            th = "".join(f"<th>{_render_inline(h)}</th>" for h in headers)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_render_inline(c)}</td>" for c in r) + "</tr>"
                for r in rows
            )
            parts.append(
                f'<div class="table-wrap item" data-caution="{str(caution).lower()}">'
                f"<table><thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table></div>"
            )
            continue

        # 引用块（多行连续 > 合并为一个 blockquote）
        if s.startswith(">"):
            flush_para()
            close_lists()
            quote_buf.append(s.lstrip(">").strip())
            i += 1
            continue
        flush_quote()

        # 有序/无序列表（支持缩进嵌套）
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        ul_m = re.match(r"^[-*+]\s+(.+)$", s)
        ol_m = re.match(r"^\d+\.\s+(.+)$", s)
        if ul_m or ol_m:
            flush_para()
            tag = "ul" if ul_m else "ol"
            item_text = ul_m.group(1) if ul_m else ol_m.group(1)
            if not list_stack or indent > list_stack[-1][1]:
                parts.append(f"<{tag}>")
                list_stack.append((tag, indent))
            else:
                close_lists(indent)
                if not list_stack or list_stack[-1][0] != tag:
                    if list_stack:
                        old_tag, _ = list_stack.pop()
                        parts.append(f"</{old_tag}>")
                    parts.append(f"<{tag}>")
                    list_stack.append((tag, indent))
            caution = _is_caution(item_text) or heading_caution
            if caution:
                caution_count += 1
            cls = "item caution" if caution else "item"
            parts.append(f'<li class="{cls}" data-caution="{str(caution).lower()}">{_render_inline(item_text)}</li>')
            i += 1
            continue

        close_lists()
        para_buf.append(s)
        i += 1

    flush_para()
    flush_quote()
    close_lists()
    return "\n".join(parts), caution_count


def render_card_html(
    markdown_text: str,
    *,
    title: str = "人话解码报告",
    source: str = "",
    meta_note: str = "",
) -> tuple[str, dict[str, int]]:
    """把解码报告 Markdown 渲染成零依赖单页 HTML。返回 (html_str, stats)。"""
    sections = _parse_sections(markdown_text)
    rendered_sections: list[str] = []
    total_cautions = 0

    for idx, sec in enumerate(sections):
        heading = sec["heading"]
        is_lead_section = "一句话" in heading
        # 无 H2 标题的自然段报告：保护首段结论在筛选模式下不隐藏
        mark_first_lead = idx == 0 and not heading
        body_html, cautions = _render_blocks(sec["lines"], heading, mark_first_lead=mark_first_lead)
        total_cautions += cautions
        sec_cls = "card-section lead-section" if is_lead_section else "card-section"
        h_html = f"<h2>{html.escape(heading)}</h2>" if heading else ""
        has_caution = "true" if (cautions > 0 or is_lead_section or mark_first_lead) else "false"
        rendered_sections.append(
            f'<section class="{sec_cls}" data-has-caution="{has_caution}">\n{h_html}\n{body_html}\n</section>'
        )

    safe_title = html.escape(title.strip() or "人话解码报告")
    meta_bits: list[str] = [f"生成日期：{date.today().isoformat()}"]
    if source.strip():
        meta_bits.append(f"来源：{html.escape(source.strip())}")
    if meta_note.strip():
        meta_bits.append(html.escape(meta_note.strip()))
    meta_html = " · ".join(meta_bits)

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{safe_title} · 人话解码器</title>
<style>
  :root {{
    --bg: #f6f7f9;
    --card: #ffffff;
    --fg: #181c22;
    --muted: #5c6573;
    --border: #e3e7ee;
    --accent: #1d4ed8;
    --accent-soft: #eff6ff;
    --warn-bg: #fff7ed;
    --warn-fg: #9a3412;
    --warn-border: #fdba74;
    --ok-bg: #ecfdf5;
    --ok-fg: #065f46;
    --unv-bg: #f3f4f6;
    --unv-fg: #374151;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0f1318;
      --card: #171c24;
      --fg: #edf1f7;
      --muted: #9aa4b2;
      --border: #27303d;
      --accent: #60a5fa;
      --accent-soft: #172554;
      --warn-bg: #2c1a0e;
      --warn-fg: #fdba74;
      --warn-border: #9a3412;
      --ok-bg: #06281e;
      --ok-fg: #6ee7b7;
      --unv-bg: #232a35;
      --unv-fg: #cbd5e1;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 24px 16px 48px;
    background: var(--bg);
    color: var(--fg);
    font: 16px/1.7 -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  }}
  .shell {{
    max-width: 760px;
    margin: 0 auto;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 28px 28px 24px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.05);
  }}
  header {{
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 20px;
  }}
  .brand {{
    font-size: 12px;
    letter-spacing: 0.06em;
    color: var(--accent);
    font-weight: 600;
    margin-bottom: 6px;
  }}
  h1 {{
    margin: 0 0 8px;
    font-size: 22px;
    line-height: 1.35;
  }}
  .meta {{
    font-size: 13px;
    color: var(--muted);
  }}
  .toolbar {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 14px;
  }}
  .btn {{
    appearance: none;
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--fg);
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
    cursor: pointer;
  }}
  .btn.active {{
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--accent);
    font-weight: 600;
  }}
  .card-section {{
    margin-bottom: 20px;
  }}
  .card-section h2 {{
    margin: 0 0 10px;
    font-size: 16px;
    color: var(--fg);
    border-left: 3px solid var(--accent);
    padding-left: 10px;
  }}
  blockquote.lead {{
    margin: 0;
    padding: 12px 14px;
    background: var(--accent-soft);
    border-radius: 10px;
    font-weight: 500;
  }}
  p {{ margin: 0 0 10px; }}
  ul, ol {{ margin: 0 0 10px; padding-left: 20px; }}
  li {{ margin-bottom: 6px; }}
  .item.caution {{
    border-left: 2px solid var(--warn-border);
    padding-left: 10px;
  }}
  .badge {{
    display: inline-block;
    font-size: 12px;
    font-weight: 600;
    padding: 1px 7px;
    border-radius: 999px;
    vertical-align: 1px;
  }}
  .badge-ok {{ background: var(--ok-bg); color: var(--ok-fg); }}
  .badge-warn {{ background: var(--warn-bg); color: var(--warn-fg); }}
  .badge-unverified {{ background: var(--unv-bg); color: var(--unv-fg); }}
  code {{
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 0.9em;
    background: var(--unv-bg);
    padding: 1px 5px;
    border-radius: 4px;
  }}
  pre {{
    margin: 0 0 12px;
    padding: 12px 14px;
    background: var(--unv-bg);
    border-radius: 8px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.5;
  }}
  pre code {{ background: transparent; padding: 0; }}
  hr.divider {{ border: none; border-top: 1px solid var(--border); margin: 16px 0; }}
  .table-wrap {{ overflow-x: auto; margin: 0 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
  th {{ background: var(--unv-bg); font-weight: 600; }}
  a {{ color: var(--accent); word-break: break-all; }}
  body.filter-caution .card-section:not(.lead-section) .item[data-caution="false"]:not(.lead-item) {{ display: none; }}
  body.filter-caution .card-section[data-has-caution="false"] {{ display: none; }}
  footer {{
    border-top: 1px solid var(--border);
    padding-top: 14px;
    margin-top: 24px;
    font-size: 12px;
    color: var(--muted);
  }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .shell {{ box-shadow: none; border: none; max-width: 100%; padding: 0; }}
    .toolbar {{ display: none; }}
  }}
</style>
</head>
<body>
  <main class="shell">
    <header>
      <div class="brand">人话解码器 · PLAIN-TALK DECODER</div>
      <h1>{safe_title}</h1>
      <div class="meta">{meta_html}</div>
      <div class="toolbar">
        <button class="btn active" id="btnAll" type="button">看全文</button>
        <button class="btn" id="btnCaution" type="button">只看存疑与避坑（{total_cautions}）</button>
        <button class="btn" id="btnPrint" type="button">打印 / 存为 PDF</button>
      </div>
    </header>
    {"".join(rendered_sections)}
    <footer>
      本卡片由 <code>dyz-plain-talk-decoder</code> 本地生成，基于可得证据分析，不构成医疗、投资或法律专业意见。
    </footer>
  </main>
<script>
  (function () {{
    var allBtn = document.getElementById("btnAll");
    var cautionBtn = document.getElementById("btnCaution");
    var printBtn = document.getElementById("btnPrint");
    allBtn.addEventListener("click", function () {{
      document.body.classList.remove("filter-caution");
      allBtn.classList.add("active");
      cautionBtn.classList.remove("active");
    }});
    cautionBtn.addEventListener("click", function () {{
      document.body.classList.add("filter-caution");
      cautionBtn.classList.add("active");
      allBtn.classList.remove("active");
    }});
    printBtn.addEventListener("click", function () {{ window.print(); }});
  }})();
</script>
</body>
</html>
"""
    return page, {"sections": len(sections), "caution_items": total_cautions}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将解码报告渲染为单页 HTML 分享卡片")
    parser.add_argument("--file", default="", help="输入 Markdown 报告路径；留空则读取标准输入")
    parser.add_argument("--output", required=True, help="输出 HTML 文件路径")
    parser.add_argument("--title", default="人话解码报告", help="卡片标题")
    parser.add_argument("--source", default="", help="来源说明（如公众号名/链接域名）")
    parser.add_argument("--meta", default="", help="附加元信息（如标准版·未联网核查）")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        args = build_parser().parse_args(argv)
        if args.file:
            src_path = Path(args.file)
            if not src_path.is_file():
                result: dict[str, Any] = {
                    "status": "failed",
                    "output_path": "",
                    "user_hint": f"找不到输入报告文件：{args.file}",
                }
                sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
                return 0
            md = src_path.read_text(encoding="utf-8")
        else:
            md = sys.stdin.read()

        if not md.strip():
            result = {
                "status": "failed",
                "output_path": "",
                "user_hint": "输入报告内容为空，无法生成分享卡片。",
            }
        else:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            html_doc, stats = render_card_html(md, title=args.title, source=args.source, meta_note=args.meta)
            out_path.write_text(html_doc, encoding="utf-8")
            result = {
                "status": "success",
                "output_path": str(out_path.resolve()),
                "sections": stats["sections"],
                "caution_items": stats["caution_items"],
                "user_hint": f"已生成离线单页分享卡片：{out_path.name}（用浏览器直接打开即可查看或存为 PDF）。",
            }
    except SystemExit:
        result = {
            "status": "failed",
            "output_path": "",
            "user_hint": "参数不完整，请提供 --output 输出路径。",
        }
    except Exception as exc:
        print(f"[render_card] 意外错误：{type(exc).__name__}", file=sys.stderr)
        result = {
            "status": "failed",
            "output_path": "",
            "user_hint": "生成分享卡片时出错，但不影响直接使用文字版报告。",
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
