"""lint_report.py 单元测试（零外部依赖，离线）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "dyz-plain-talk-decoder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lint_report as lr  # noqa: E402


def _make_standard_report(extra: str = "") -> str:
    return (
        "## 一句话讲人话\n"
        "这是某品牌新机型的推广介绍，续航标称较高但未说明测试条件，建议先别急着付定金。\n\n"
        "## 事实与拆解\n"
        "原文强调最高续航数字，但没有交代测试亮度、网络环境、后台负载和对比机型，也没有公布起售版本价格、首批供货地区与交付时间。"
        "屏幕尺寸、刷新率、电池容量和机身重量给出了明确数值，可以对照官方规格表逐项核验。"
        "整体信息交代了一部分基础规格，但决定下单的关键定价与使用条件还缺着，不能单凭极值做判断。\n\n"
        "## 核查与可信度\n"
        "以上数字均来自你贴的推广页，未联网核查；材料本身没有内部矛盾，但关键前提缺失。\n\n"
        "## 给你的建议\n"
        "先问客服两件事：续航是连续播视频还是待机测的？起售版本具体是什么配置和价格？"
        "拿到书面答复并等第三方实测后再决定要不要下单。"
        + extra
    )


class TestCountVisibleChars:
    def test_strips_markdown_and_urls(self):
        md = "## 标题\n- 查看[来源](https://example.com/a/b/c?x=1)与 **数字 123** https://foo.bar/baz\n"
        vis, hanzi = lr.count_visible_chars(md)
        # 标题查看来源与数字123 -> 12 个可见字符，9 个汉字
        assert vis == 12
        assert hanzi == 9


class TestLintReport:
    def test_clean_standard_offline_report_passes(self):
        r = lr.lint_report(_make_standard_report(), tier="standard", offline=True)
        assert r["ok"] is True
        assert r["errors"] == []
        assert r["warnings"] == []
        assert 300 <= r["visible_chars"] <= 600

    def test_fast_tier_over_limit_fails(self):
        long_fast = "这是一段超长的极速版回答。" * 25 + "（未核查）"
        r = lr.lint_report(long_fast, tier="fast", offline=True)
        assert r["ok"] is False
        assert any(e["code"] == "too_long" for e in r["errors"])

    def test_offline_three_state_label_rejected(self):
        bad = _make_standard_report("\n补记：续航宣称 `[已核实]`。")
        r = lr.lint_report(bad, tier="standard", offline=True)
        assert r["ok"] is False
        assert any(e["code"] == "offline_three_state_label" for e in r["errors"])

    def test_offline_missing_disclosure_warns(self):
        text = _make_standard_report().replace("未联网核查", "暂不展开")
        r = lr.lint_report(text, tier="standard", offline=True)
        assert r["ok"] is True
        assert any(w["code"] == "missing_offline_disclosure" for w in r["warnings"])

    def test_high_risk_overreach_and_missing_boundary_both_caught(self):
        bad = _make_standard_report("\n结论：建议你买入这只基金。")
        r = lr.lint_report(bad, tier="standard", offline=True, high_risk=True)
        codes = {e["code"] for e in r["errors"]}
        assert "high_risk_overreach" in codes
        assert "missing_high_risk_boundary" in codes

    def test_high_risk_with_boundary_passes(self):
        good = _make_standard_report("\n涉及理财决策，不替你做最终决定，建议咨询持牌理财顾问。")
        r = lr.lint_report(good, tier="standard", offline=True, high_risk=True)
        assert r["ok"] is True

    def test_internal_jargon_leak_warns(self):
        leaked = _make_standard_report("\n备注：本篇按消费销售拆，命中 S2 场景卡。")
        r = lr.lint_report(leaked, tier="standard", offline=True)
        assert any(w["code"] == "internal_jargon_leak" for w in r["warnings"])

    def test_profile_leak_in_search_and_facts_caught(self):
        report = _make_standard_report()
        # 故意把画像词塞进“事实与拆解”
        leaked_report = report.replace("没有交代测试亮度", "针对你在杭州预算3000元的条件，没有交代测试亮度")
        r = lr.lint_report(
            leaked_report,
            tier="standard",
            offline=True,
            profile_terms=["杭州", "预算3000元"],
            search_queries=["某品牌续航实测 杭州"],
        )
        codes = {e["code"] for e in r["errors"]}
        assert "profile_leak_in_search" in codes
        assert "profile_leak_in_facts" in codes
        # 错误信息不能回显用户画像原词
        msg = json.dumps(r, ensure_ascii=False)
        assert "杭州" not in msg and "预算3000元" not in msg

    def test_profile_only_in_advice_section_passes(self):
        report = _make_standard_report("\n按你说的预算3000元以内，这款定价还没公布，先等价格出来。")
        r = lr.lint_report(
            report,
            tier="standard",
            offline=True,
            profile_terms=["预算3000元"],
            search_queries=["某品牌续航实测"],
        )
        assert r["ok"] is True

    def test_cli_reads_file_and_exits_zero(self, tmp_path):
        f = tmp_path / "r.md"
        f.write_text(_make_standard_report(), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "lint_report.py"), "--file", str(f), "--tier", "standard", "--offline"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["ok"] is True

    def test_m_series_chips_and_money_supply_not_false_positive(self):
        """Apple M2 芯片与货币 M1/M2 不得误报为内部术语泄露（P1-2）。"""
        report = _make_standard_report("\n对比说明：搭载苹果 M2 与 M3 芯片，同时参考货币供应 M1 与 M2 增速。")
        r = lr.lint_report(report, tier="standard", offline=True)
        assert not any(w["code"] == "internal_jargon_leak" for w in r["warnings"])

    def test_chinese_adjacent_internal_jargon_still_caught(self):
        """中文紧贴无空格的 M2/S2 内部术语泄露必须检出（P1-2）。"""
        report = _make_standard_report("\n备注：本篇按M2通用思路和S2卡来拆解。")
        r = lr.lint_report(report, tier="standard", offline=True)
        assert any(w["code"] == "internal_jargon_leak" for w in r["warnings"])

    def test_negated_high_risk_advice_not_false_positive(self):
        """“不建议你买入这只基金”等合规否定提醒不得误杀（P1-3）。"""
        report = _make_standard_report("\n风险提示：这里绝不建议你买入这只基金，也不推荐直接服用这种药，请咨询持牌顾问与医生。")
        r = lr.lint_report(report, tier="standard", offline=True, high_risk=True)
        assert r["ok"] is True, r

    def test_plain_paragraph_report_checks_profile_leak_in_facts(self):
        """推荐的无 H2 自然段写法（写法 A）同样必须拦截事实段泄露画像（P1-4）。"""
        leaked = (
            "可以继续了解，但先别急着付钱。针对你在杭州预算3000元的情况，厂标速度只是空载极值，不是日常表现。"
            + "关键条款和对比口径都还有缺口，需要拿到书面报价与活动细则。" * 4
            + "\n\n"
            "上月意向登记不是成交量，定金排序和退费规则均未写清。以上仅据商家自述，未联网核查。"
            + "\n\n"
            "建议先要书面报价并带常用带材试跑后再决定。"
        )
        r = lr.lint_report(leaked, tier="standard", offline=True, profile_terms=["杭州", "预算3000元"])
        assert any(e["code"] == "profile_leak_in_facts" for e in r["errors"])

        clean = (
            "可以继续了解，但先别急着付钱。厂标速度只是空载极值，不是日常表现。"
            + "关键条款和对比口径都还有缺口，需要拿到书面报价与活动细则。" * 4
            + "\n\n"
            "上月意向登记不是成交量，定金排序和退费规则均未写清。以上仅据商家自述，未联网核查。"
            + "\n\n"
            "按你说的在杭州预算3000元以内，建议先要书面报价并带常用带材试跑后再决定。"
        )
        r2 = lr.lint_report(clean, tier="standard", offline=True, profile_terms=["杭州", "预算3000元"])
        assert not any(e["code"] == "profile_leak_in_facts" for e in r2["errors"])

    def test_cli_stdin_utf8_chinese_works(self):
        """通过 stdin 传入 UTF-8 中文报告在 Windows 下也必须无编码报错（P1-9）。"""
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "lint_report.py"), "--tier", "standard", "--offline"],
            input=_make_standard_report(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["ok"] is True

    def test_all_public_examples_pass_lint(self):
        """examples/ 下全部公开样板必须通过自家门禁脚本（P1-10）。"""
        specs = [
            ("01-self-audit-readme.md", "standard", False),
            ("02-product-launch-and-promo.md", "standard", False),
            ("03-prepaid-and-finance.md", "standard", True),
            ("04-official-notice.md", "fast", False),
            ("05-clean-article-no-nitpick.md", "standard", False),
        ]
        for name, tier, high_risk in specs:
            raw = (ROOT / "examples" / name).read_text(encoding="utf-8")
            body = raw.split("\n---\n", 1)[1].strip()
            r = lr.lint_report(body, tier=tier, offline=True, high_risk=high_risk)
            assert r["ok"] is True, f"{name} 未通过门禁：{r}"
