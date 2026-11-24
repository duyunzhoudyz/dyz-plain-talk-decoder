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
5. 仅用合成夹具的画像词（--profile-terms）和搜索词（--search-queries / --search-query）检查是否串入搜索词或事实段。

禁止向脚本传入真实画像、真实画像词或含真实画像的原答（包括通过 --file 或标准输入）。
有真实画像的报告请在宿主按手册自检；脚本只检查无真实画像的文本或已脱敏片段，不把片段当全文。
`ok` 仅表示未触发确定性错误，不代替语义复核。

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

_SENTENCE_PUNCT = r"。！？；;.!?\r\n"
_CLAUSE_PUNCT = _SENTENCE_PUNCT + "，,"
SENTENCE_BREAK_RE = re.compile(rf"[{_SENTENCE_PUNCT}]")
CLAUSE_BREAK_RE = re.compile(rf"[{_CLAUSE_PUNCT}]")

# 裸“内容/说明/介绍”不表明来源，只有明确指代或具体材料才算。
_OFFLINE_THIS = r"(?:这[份张条纸段篇则]|所[附贴]|该)"
_OFFLINE_MATERIAL = (
    r"(?:宣传(?:页|稿|单|文案)|销售(?:页|文案)|推广页|价目表|公告|通知|通稿|公报|材料|原文|附文|记录|简报|告示|手记|"
    r"随笔|原帖|帖[子文]|合同|报价单|活动单|工作单|截图|资料|海报)"
)
_OFFLINE_QUALIFIER = r"[\u3400-\u9fffA-Za-z0-9]{0,12}?"
_OFFLINE_NAMED = rf"{_OFFLINE_THIS}{_OFFLINE_QUALIFIER}(?:{_OFFLINE_MATERIAL}|介绍|说明|文字|内容)"
_OFFLINE_ISSUER = r"(?:厂方|馆方|经销商|商家|产品)"
OFFLINE_ACK_RE = re.compile(
    rf"(?:[仅只单光]?[按看凭据]|就)[ \t]*{_OFFLINE_QUALIFIER}[ \t]*{_OFFLINE_NAMED}"
    rf"|{_OFFLINE_NAMED}(?:里|中|上)"
    rf"|[仅只单光][看按凭据](?:{_OFFLINE_ISSUER}(?:介绍|说明)|(?:{_OFFLINE_ISSUER}|虚构|现有|上述)?{_OFFLINE_MATERIAL}|"
    rf"{_OFFLINE_THIS}(?=[ \t：:]*$))"
    rf"|(?:[按就]|[仅只单光](?:[看按凭据]|对照)了?|[只都均]来自)你"
    rf"(?:[贴附]的|给的(?:{_OFFLINE_NAMED}|{_OFFLINE_MATERIAL}))"
    r"|(?:未|没(?:有)?)(?:联网(?:核查)?|核查|查外部|外查|另查|查过|[作做]外部(?:查证|核查|核实|核对|核验))"
    r"|无法联网|只做(?:了)?题内对照"
)
OFFLINE_NEGATION_RE = re.compile(r"(?:并非|不(?:[仅只单光]?是)?|别|勿)(?:说(?:我(?:们)?)?)?[ \t：:]*[“‘「『\"']?$")

# 高风险领域的明确越界决策表述（分句级排除否定前缀，如“绝不建议/也不推荐直接服用”不误杀）
INVESTMENT = r"(?:基金|股票|理财|保险)"
INVESTMENT_ACTION = r"(?:买入|入手|购买|买|重仓|加仓|抛售|卖出)"
INVESTMENT_DIRECTIVE = r"(?:(?:建议|推荐|可以|直接|值得)(?:你)?)"
TREATMENT_DIRECTIVE = r"(?:可以放心|直接|建议你|推荐你)"
TREATMENT_ACTION = r"(?:服用|吃|停用|停吃)"
HIGH_RISK_OVERREACH_PATTERNS = [
    re.compile(rf"{INVESTMENT_DIRECTIVE}{INVESTMENT_ACTION}(?:这只|这款|该|这个)?(?:{INVESTMENT}|产品)"),
    re.compile(rf"{INVESTMENT_DIRECTIVE}(?:把|将)(?:这只|这款|该|这个)?(?:{INVESTMENT}|产品){INVESTMENT_ACTION}"),
    re.compile(rf"(?:千万)?(?:别买|不要买)(?:这只|这款|该|这个){INVESTMENT}"),
    re.compile(rf"{TREATMENT_DIRECTIVE}{TREATMENT_ACTION}(?:这个|这种|该)?药"),
    re.compile(rf"(?:这只|这款|该|这个){INVESTMENT}\s*[，,]?\s*(?:{INVESTMENT_DIRECTIVE}{INVESTMENT_ACTION}|(?:千万)?(?:别买|不要买))"),
    re.compile(rf"(?:这个|这种|该)药\s*[，,]?\s*{TREATMENT_DIRECTIVE}{TREATMENT_ACTION}"),
    re.compile(r"(?:可以放心签|直接签吧|赶紧签|千万别签这份合同)"),
]
CLAUSE_NEGATION_RE = re.compile(
    r"(?:并不是|不是|并非|并未|没有|绝不|切勿|不要|不能|不可|不必|无需|不用|也不|并不|别|勿|不|未|没)"
    r"[ \t]*(?:(?:(?:推荐|建议|鼓励|主张|需要)你?|替你|请|应当?|应该|打算|先|再|去)[ \t]*){0,4}$"
)
PROFESSIONAL = (
    r"(?:(?:(?:有|具备)执业资格的|有资质的|合格的)?(?:执业|专业)?(?:医生|医师|药师|律师|法律从业者)|"
    r"持牌(?:理财顾问|投资顾问|投顾|顾问|专业人士)|理财顾问|投资顾问|专业人士)"
    r"(?!的?(?:照片|画像|身份))"
)
_PROFESSIONAL_MODIFIER = rf"(?:(?:(?!而非|并非|不是|不具备|没有)[^{_CLAUSE_PUNCT}：:]){{1,12}}的|当地|本地|附近|社区|值班)?"
HIGH_RISK_BOUNDARY_RE = re.compile(
    rf"(?:咨询|请教|询问|求助于|找|核对|问问?)[ \t]*{_PROFESSIONAL_MODIFIER}{PROFESSIONAL}|"
    rf"(?:请|向|让|跟|和|与|由)[ \t]*{_PROFESSIONAL_MODIFIER}{PROFESSIONAL}"
    rf"[^{_CLAUSE_PUNCT}：:]{{0,12}}(?:核对|复核|确认|评估|咨询|把关|审阅|问清)|"
    r"(?:不构成|不是|不(?:能)?代替)(?:专业|投资|理财|医疗|用药|法律)(?:建议|意见|诊断)"
)

SOURCE = r"(?:原文|原帖|材料|文案|文章|报道|海报|宣传(?:页|文案)?|广告(?:页|文案)?|公告|价目表|商家|销售方|对方|作者)"
_SOURCE_INTRO = (
    rf"(?:^|(?<=[{_CLAUSE_PUNCT}]))[ \t>\-]*{SOURCE}(?:原话|原句)?[ \t]*[:：]|"
    rf"{SOURCE}(?:里|中)?(?:明确|还|也)?"
    rf"(?:写(?:着|道|的是|有|明|了)?|说(?:道|的是|了)?|(?:声|宣)?称|鼓吹|提到|原(?:话|句)是|"
    rf"(?:把|将)[^{_CLAUSE_PUNCT}]{{1,24}}标(?:注|记)?(?:为|成)|标(?:注|记)?(?:为|成|着|了)?|"
    r"的?(?:标签|标注)(?:是|为)?|使用了?|用了|引用了?)[ \t]*[:：]?"
)
SOURCE_ATTRIBUTION_RE = re.compile(rf"(?:{_SOURCE_INTRO}|(?:原文|原帖)(?:里|中)?的)[ \t]*(?:>[ \t]*)?$")
UNQUOTED_SOURCE_RE = re.compile(rf"(?:{_SOURCE_INTRO})[ \t]*[，,]?[ \t]*[^{_CLAUSE_PUNCT}]+")
SOURCE_ATTRIBUTION_AFTER_RE = re.compile(
    rf"^\s*(?:[，,]\s*)?(?:这(?:句话|句|个标签|个标注))?(?:来自|出自|是){SOURCE}(?:的)?(?:原话|原句|标签|标注)"
)
QUOTED_FRAGMENT_RE = re.compile(
    r'''“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』|"[^"\n]*"|'[^'\n]*'|`[^`\n]*`|'''
    + "|".join(map(re.escape, THREE_STATE_LABELS))
)
REJECTION_BEFORE_RE = re.compile(
    r"(?:不(?:认同|同意|认可|支持|接受|采纳|建议|赞同)|反对|驳回|"
    r"(?:不能|不应|不要|别)(?:据此)?(?:把|说成|采纳|接受|得出|下结论说))\s*[:：]?\s*$"
)
REJECTION_AFTER_RE = re.compile(
    r"^\s*(?:[，,]\s*)?(?:这(?:句话|个建议|条建议|种说法))?(?:并)?"
    r"(?:站不住脚|没有(?:足够)?依据|缺乏依据|不(?:成立|靠谱|可信|可取)|不能(?:作为|当作)(?:结论|建议|依据))"
)

# 面向用户的输出不应暴露的内部工作流术语（精准匹配内部词，避免误报 Apple M2 芯片、货币 M1/M2）
INTERNAL_JARGON_PATTERNS = [
    re.compile(r"按消费销售(?:来)?拆"),
    re.compile(r"通用五问"),
    re.compile(r"场景卡"),
    re.compile(r"(?:主模式|辅模式)"),
    re.compile(r"(?<![A-Za-z0-9])M[1-5]\s*[+＋/]\s*S[1-5](?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])M[1-5]\s*(?:模式|手册|通稿|话术|洗稿|脱水|热点|通用)"),
    re.compile(r"(?<![A-Za-z0-9])S[1-5]\s*(?:卡|场景)"),
]

ADVICE_SECTION_HEADING = re.compile(r"^(?:给你的建议|结论与下一步|建议|下一步)")
FACT_SECTION_HEADING = re.compile(r"^(?:事实|核查|可信度|滤镜拆解|潜台词|原文要点)")
FACT_CUE = re.compile(
    r"(?:^|[，,])\s*[*_> -]*(?:事实(?:上|是|[:：])|(?:核查|原文)[:：]|据(?:原文|原帖)|"
    r"(?:原文|原帖|材料|文案|文章|报道|报告|海报|宣传页|广告|商家|合同|公告|条款|价目表|记录|厂标|票面|退款规则)"
    r"[^，,:：]{0,14}(?:写|称|说|显示|交代|提到|列出|给出|标明|注明|意味着|只是|并非|不等于|未公布|未披露)|"
    r"(?:票价|售价|有效期|门槛|单次票|按次票|月卡|次卡|名额)(?:是|为|仅限|须|需|不退|[^，,:：]{0,8}元))"
)
PERSONAL_LEAD = re.compile(r"^\s*[*_> -]*(?:按你说|结合你|按你的|根据你的|考虑到你|针对你|你计划)")
ADVICE_CUE = re.compile(
    r"(?:^|[，,])\s*[*_> -]*(?:给你的建议|下一步|建议(?:你|先|[:：])|"
    r"(?:先|不妨)(?:问|别急|要|查|核对|确认|拿|等|看|试|向|把|做)|"
    r"(?:按你说|结合你|按你的|根据你的|考虑到你|针对你|你计划).*?(?:建议|可以|不妨|先(?:问|别|不|要|查|看|试)))"
)


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


def _author_text(report: str, *, skip_rejections: bool = False) -> str:
    """只屏蔽明确归属于原文的引述；高风险判定另放行明确驳回的说法。"""
    def omit(match: re.Match[str]) -> str:
        before, after = report[:match.start()], report[match.end():]
        if SOURCE_ATTRIBUTION_RE.search(before) or SOURCE_ATTRIBUTION_AFTER_RE.match(after):
            return "。"
        if skip_rejections and (REJECTION_BEFORE_RE.search(before) or REJECTION_AFTER_RE.match(after)):
            return "。"
        return match.group(0)

    return QUOTED_FRAGMENT_RE.sub(omit, report)


def _profile_hits(text: str, terms: list[str]) -> tuple[set[str], set[str]]:
    """区分明确事实命中与需复核的合成画像词。"""
    paragraphs = re.split(r"\n\s*\n|\n(?=#{1,6}[ \t])", text)
    facts: set[str] = set()
    review: set[str] = set()
    section = ""
    for paragraph in paragraphs:
        heading = re.match(r"^#{1,6}[ \t]+([^\n]+)", paragraph)
        if heading:
            title = heading.group(1)
            section = (
                "facts" if FACT_SECTION_HEADING.match(title) else
                "advice" if ADVICE_SECTION_HEADING.match(title) else ""
            )
        hits = {term for term in terms if term in paragraph}
        if not hits:
            continue
        if section == "facts":
            facts.update(hits)
            continue
        if len(paragraphs) == 1 and not section:
            review.update(hits)
            continue

        # 同段泛用开场不覆盖后续事实；同一句混合线索时交人工复核。
        sentences = re.split(r"[。！？；;\n]", paragraph)
        if section == "advice":
            # 建议标题或句末建议不能覆盖前面独立的事实分句。
            sentences += [
                clause for sentence in sentences if ADVICE_CUE.search(sentence) and not PERSONAL_LEAD.search(sentence)
                for clause in CLAUSE_BREAK_RE.split(sentence) if FACT_CUE.match(clause)
            ]
        for term in hits:
            containing = [sentence for sentence in sentences if term in sentence]
            if any(FACT_CUE.search(s) and not ADVICE_CUE.search(s) for s in containing):
                facts.add(term)
            elif section != "advice" and (
                not containing or any(FACT_CUE.search(s) or not (ADVICE_CUE.search(s) or PERSONAL_LEAD.search(s)) for s in containing)
            ):
                review.add(term)
    return facts, review


def lint_report(
    text: str,
    *,
    tier: str = "standard",
    offline: bool = False,
    high_risk: bool = False,
    profile_terms: Optional[list[str]] = None,
    search_queries: Optional[list[str]] = None,
) -> dict[str, Any]:
    """对不含真实画像的报告执行自检；画像词和搜索词参数仅用于合成夹具。"""
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

    # 词面门禁忽略行内强调，保留原来的分句边界。
    normalized = re.sub(r"[*_~]", "", report) if offline or high_risk else report

    # 2. 离线模式核查纪律
    if offline:
        author_report = _author_text(normalized)
        hit_labels = [lbl for lbl in THREE_STATE_LABELS if lbl in author_report]
        if hit_labels:
            errors.append({
                "code": "offline_three_state_label",
                "message": f"离线模式下禁止打联网三态标签：{'、'.join(hit_labels)}。",
            })
        if not any(
            not OFFLINE_NEGATION_RE.search(clause[:m.start()])
            for clause in CLAUSE_BREAK_RE.split(UNQUOTED_SOURCE_RE.sub("", author_report))
            for m in OFFLINE_ACK_RE.finditer(clause)
        ):
            warnings.append({
                "code": "missing_offline_disclosure",
                "message": "离线报告未说明“未联网核查”或“仅据原文/所贴材料”。",
            })

    # 3. 高风险边界（医疗、投资理财、法律）
    if high_risk:
        risk_report = UNQUOTED_SOURCE_RE.sub("", _author_text(normalized, skip_rejections=True))
        overreach_hit = next((
            m.group(0) for sentence in SENTENCE_BREAK_RE.split(risk_report) for pat in HIGH_RISK_OVERREACH_PATTERNS
            for m in pat.finditer(sentence) if not (
                CLAUSE_NEGATION_RE.search(prefix := CLAUSE_BREAK_RE.split(sentence[:m.start()])[-1])
                or REJECTION_BEFORE_RE.search(prefix)
                or (m.group(0).startswith(("建议", "推荐")) and re.fullmatch(rf"\s*{SOURCE}\s*", prefix))
            )
        ), "")
        if overreach_hit:
            errors.append({
                "code": "high_risk_overreach",
                "message": f"高风险领域疑似替用户做最终决定：「{overreach_hit}」。",
            })
        if not any(
            not CLAUSE_NEGATION_RE.search(clause[:m.start()])
            for clause in CLAUSE_BREAK_RE.split(risk_report) for m in HIGH_RISK_BOUNDARY_RE.finditer(clause)
        ):
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

    # 5. 仅用合成夹具检查画像隔离（PRD E4）
    clean_terms = [t.strip() for t in (profile_terms or []) if t and t.strip()]
    if clean_terms:
        joined_queries = "\n".join(search_queries or [])
        if joined_queries:
            leaked_in_search = [t for t in clean_terms if t in joined_queries]
            if leaked_in_search:
                errors.append({
                    "code": "profile_leak_in_search",
                    "message": f"合成画像词出现在搜索词中（命中 {len(leaked_in_search)} 项）。",
                })
        leaked_in_facts, needs_review = _profile_hits(report, clean_terms)
        if leaked_in_facts:
            errors.append({
                "code": "profile_leak_in_facts",
                "message": f"合成画像词出现在明确的事实/拆解部分（命中 {len(leaked_in_facts)} 项）。",
            })
        if needs_review:
            warnings.append({
                "code": "profile_isolation_needs_review",
                "message": f"含合成画像词的片段无法明确区分事实和建议，需语义复核（命中 {len(needs_review)} 项）。",
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
    parser = argparse.ArgumentParser(
        description="对解码报告做确定性自检并输出 JSON。禁止传入真实画像、真实画像词或含真实画像的原答。",
    )
    parser.add_argument("--file", default="", help="不含真实画像的报告文件路径；留空则从标准输入读取（同样不得含真实画像）")
    parser.add_argument("--tier", choices=("fast", "standard", "deep"), default="standard", help="详略档位")
    parser.add_argument("--offline", action="store_true", help="离线模式（禁打联网三态标签）")
    parser.add_argument("--high-risk", action="store_true", help="涉及医疗/投资理财/法律高风险边界")
    parser.add_argument("--profile-terms", default="", help="逗号分隔的合成画像词（仅用于合成夹具，不回显命中词）")
    parser.add_argument("--search-queries", "--search-query", default="", help="逗号分隔的合成搜索词（仅用于合成夹具）")
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
