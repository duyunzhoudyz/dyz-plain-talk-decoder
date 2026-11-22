#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""可选输出自检脚本（纯标准库，零外部依赖）：在 A 档环境作为第 8 步确定性门禁。

用法：
    python scripts/lint_report.py --file report.md --tier standard [--offline] [--high-risk]
    python scripts/lint_report.py --tier fast --offline < report.md

检查项：
1. 字数档位（极速 ≤150、标准 300–600、深度 800–1500，按非空白可见字符计，去除 Markdown 排版符号与 URL）；
2. 离线模式下是否误打联网三态标签（[已核实] / [存疑/有出入] / [未证实/单方说法]）；
3. 高风险领域（医疗/投资/法律）是否越界替用户做最终决定，以及是否包含专业边界提示；
4. 是否向用户泄露内部术语（如 M2/S2、按消费销售拆、通用五问、场景卡）；
5. 若传入画像私有词（--profile-terms），检查是否泄露到搜索词（--search-queries）或事实/核查分节。

约定（AGENTS.md 第 7 节）：
- 标准输出只打印一份 UTF-8 JSON，退出码恒为 0；成败由 `ok` 字段表达。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# 档位字数区间：(理想下限, 理想上限, 容忍下限, 容忍上限)
TIER_BUDGETS: dict[str, tuple[int, int, int, int]] = {
    "fast": (1, 150, 1, 180),
    "standard": (300, 600, 260, 660),
    "deep": (800, 1500, 720, 1650),
}

THREE_STATE_LABELS = (
    "[已核实]",
    "[存疑/有出入]",
    "[未证实/单方说法]",
)

OFFLINE_ACK_HINTS = (
    "未联网核查",
    "未核查",
    "未联网",
    "没联网",
    "无法联网",
    "仅据所附",
    "仅据所贴",
    "仅据原文",
    "仅据材料",
)

# 高风险领域的明确越界决策表述
HIGH_RISK_OVERREACH_PATTERNS = [
    re.compile(r"(建议|推荐|可以|直接)(你)?(买入|入手|购买|重仓|加仓|抛售|卖出)(这只|该)?(基金|股票|理财|保险)"),
    re.compile(r"(别买|不要买)(这只|该)(基金|股票|理财|保险)"),
    re.compile(r"(可以放心|直接|建议你)(服用|吃)(这个|这种|该)药"),
    re.compile(r"(可以放心签|直接签吧|赶紧签|千万别签这份合同)"),
]

HIGH_RISK_BOUNDARY_HINTS = (
    "持牌",
    "医生",
    "医师",
    "律师",
    "专业人士",
    "顾问",
    "不是法律意见",
    "不构成投资建议",
    "不构成医疗建议",
    "不替你做最终决定",
    "不代替专业",
)

# 面向用户的输出不应暴露的内部工作流术语
INTERNAL_JARGON_PATTERNS = [
    re.compile(r"按消费销售拆"),
    re.compile(r"通用五问"),
    re.compile(r"场景卡"),
    re.compile(r"\bM[1-5]\b"),
    re.compile(r"\bS[1-5]\b"),
]

FACT_SECTION_HEADING = re.compile(
    r"^#{1,3}\s*(核心事实|事实与拆解|滤镜拆解|核查与可信度|可信度与事实核查|逐句核查)",
    re.MULTILINE,
)
ADVICE_SECTION_HEADING = re.compile(r"^#{1,3}\s*(给你的建议|结论与下一步|建议)", re.MULTILINE)


def count_visible_chars(markdown_text: str) -> tuple[int, int]:
    """按 style-plain-talk.md 口径统计：(非空白可见字符数, 汉字数)。

    去除链接目标 URL、Markdown 排版标记（# * > ` - [ ] 等），保留正文、标题文字、数字和标点。
    """
    text = markdown_text or ""
    # [文字](url) -> 文字
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 裸 URL 不计入字数
    text = re.sub(r"https?://\S+", "", text)
    # 行首标题、引用、列表符号
    text = re.sub(r"(?m)^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)", "", text)
    # 行内强调、代码反引号、表格分隔线
    text = re.sub(r"(?m)^\s*\|?[\s:-]+\|[\s|:-]*$", "", text)
    text = re.sub(r"[`*_~|]", "", text)
    visible = re.sub(r"\s+", "", text)
    hanzi = re.findall(r"[\u4e00-\u9fff]", visible)
    return len(visible), len(hanzi)


def _split_fact_part(text: str) -> str:
    """若报告分节且存在“给你的建议”节，取建议节之前的正文用于检测画像越界。"""
    m = ADVICE_SECTION_HEADING.search(text)
    if not m:
        return ""
    return text[: m.start()]


def lint_report(
    text: str,
    *,
    tier: str = "standard",
    offline: bool = False,
    high_risk: bool = False,
    profile_terms: Optional[list[str]] = None,
    search_queries: Optional[list[str]] = None,
) -> dict[str, Any]:
    """对解码报告执行确定性自检。返回结构化结果字典。"""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    report = (text or "").strip()

    if not report:
        return {
            "ok": False,
            "tier": tier,
            "visible_chars": 0,
            "hanzi_chars": 0,
            "errors": [{"code": "empty_report", "message": "报告正文为空。"}],
            "warnings": [],
        }

    visible_chars, hanzi_chars = count_visible_chars(report)

    # 1. 字数档位检查
    lo, hi, hard_lo, hard_hi = TIER_BUDGETS.get(tier, TIER_BUDGETS["standard"])
    if visible_chars > hard_hi:
        errors.append({
            "code": "too_long",
            "message": f"{tier} 档目标 {lo}–{hi} 字，当前 {visible_chars} 字，明显超长。",
        })
    elif visible_chars > hi:
        warnings.append({
            "code": "slightly_long",
            "message": f"{tier} 档目标 {lo}–{hi} 字，当前 {visible_chars} 字，略偏长。",
        })
    elif visible_chars < hard_lo:
        errors.append({
            "code": "too_short",
            "message": f"{tier} 档目标 {lo}–{hi} 字，当前 {visible_chars} 字，明显偏短。",
        })
    elif visible_chars < lo:
        warnings.append({
            "code": "slightly_short",
            "message": f"{tier} 档目标 {lo}–{hi} 字，当前 {visible_chars} 字，略偏短。",
        })

    # 2. 离线模式核查纪律
    if offline:
        hit_labels = [lbl for lbl in THREE_STATE_LABELS if lbl in report]
        if hit_labels:
            errors.append({
                "code": "offline_three_state_label",
                "message": f"离线模式下禁止打联网三态标签：{'、'.join(hit_labels)}。",
            })
        if not any(hint in report for hint in OFFLINE_ACK_HINTS):
            warnings.append({
                "code": "missing_offline_disclosure",
                "message": "离线报告未说明“未联网核查”或“仅据原文/所贴材料”。",
            })

    # 3. 高风险边界（医疗、投资理财、法律）
    if high_risk:
        for pat in HIGH_RISK_OVERREACH_PATTERNS:
            m = pat.search(report)
            if m:
                errors.append({
                    "code": "high_risk_overreach",
                    "message": f"高风险领域疑似替用户做最终决定：「{m.group(0)}」。",
                })
                break
        if not any(hint in report for hint in HIGH_RISK_BOUNDARY_HINTS):
            errors.append({
                "code": "missing_high_risk_boundary",
                "message": "高风险领域缺少咨询持牌专业人士或非专业意见边界提示。",
            })

    # 4. 内部术语外泄
    leaked_jargon: list[str] = []
    for pat in INTERNAL_JARGON_PATTERNS:
        m = pat.search(report)
        if m:
            leaked_jargon.append(m.group(0))
    if leaked_jargon:
        warnings.append({
            "code": "internal_jargon_leak",
            "message": f"面向用户的输出包含内部术语：{'、'.join(leaked_jargon)}。",
        })

    # 5. 用户画像隔离校验（PRD E4）
    clean_terms = [t.strip() for t in (profile_terms or []) if t and t.strip()]
    if clean_terms:
        joined_queries = "\n".join(search_queries or [])
        if joined_queries:
            leaked_in_search = [t for t in clean_terms if t in joined_queries]
            if leaked_in_search:
                errors.append({
                    "code": "profile_leak_in_search",
                    "message": f"用户画像信息泄露进联网搜索词（命中 {len(leaked_in_search)} 项）。",
                })
        fact_part = _split_fact_part(report)
        if fact_part and FACT_SECTION_HEADING.search(fact_part):
            leaked_in_facts = [t for t in clean_terms if t in fact_part]
            if leaked_in_facts:
                errors.append({
                    "code": "profile_leak_in_facts",
                    "message": f"用户画像信息出现在建议节之前的事实/拆解部分（命中 {len(leaked_in_facts)} 项）。",
                })

    return {
        "ok": len(errors) == 0,
        "tier": tier,
        "visible_chars": visible_chars,
        "hanzi_chars": hanzi_chars,
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对解码报告做确定性自检并输出 JSON")
    parser.add_argument("--file", default="", help="报告文件路径；留空则从标准输入读取")
    parser.add_argument("--tier", choices=("fast", "standard", "deep"), default="standard", help="详略档位")
    parser.add_argument("--offline", action="store_true", help="离线模式（禁打联网三态标签）")
    parser.add_argument("--high-risk", action="store_true", help="涉及医疗/投资理财/法律高风险边界")
    parser.add_argument("--profile-terms", default="", help="逗号分隔的画像私有检查词（仅本地校验，不输出原文）")
    parser.add_argument("--search-queries", default="", help="逗号分隔的本次联网搜索词，用于校验画像零泄漏")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        args = build_parser().parse_args(argv)
        if args.file:
            path = Path(args.file)
            if not path.is_file():
                result: dict[str, Any] = {
                    "ok": False,
                    "tier": args.tier,
                    "visible_chars": 0,
                    "hanzi_chars": 0,
                    "errors": [{"code": "file_not_found", "message": f"找不到报告文件：{args.file}"}],
                    "warnings": [],
                }
            else:
                text = path.read_text(encoding="utf-8")
                result = lint_report(
                    text,
                    tier=args.tier,
                    offline=args.offline,
                    high_risk=args.high_risk,
                    profile_terms=[s for s in args.profile_terms.split(",") if s.strip()],
                    search_queries=[s for s in args.search_queries.split(",") if s.strip()],
                )
        else:
            text = sys.stdin.read()
            result = lint_report(
                text,
                tier=args.tier,
                offline=args.offline,
                high_risk=args.high_risk,
                profile_terms=[s for s in args.profile_terms.split(",") if s.strip()],
                search_queries=[s for s in args.search_queries.split(",") if s.strip()],
            )
    except SystemExit:
        result = {
            "ok": False,
            "tier": "standard",
            "visible_chars": 0,
            "hanzi_chars": 0,
            "errors": [{"code": "invalid_args", "message": "参数错误，请检查 --tier / --file 等选项。"}],
            "warnings": [],
        }
    except Exception as exc:
        print(f"[lint_report] 意外错误：{type(exc).__name__}", file=sys.stderr)
        result = {
            "ok": False,
            "tier": "standard",
            "visible_chars": 0,
            "hanzi_chars": 0,
            "errors": [{"code": "internal_error", "message": "自检脚本运行出错，不影响按手册人工核对。"}],
            "warnings": [],
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
