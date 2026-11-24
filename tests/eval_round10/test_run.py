"""合成首答、复述和票据走全流程；R8 文件只读，真实 AI 评审不在测试内。"""

from collections import Counter
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("r10_runner", Path(__file__).with_name("run.py"))
assert SPEC is not None and SPEC.loader is not None
run = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run
SPEC.loader.exec_module(run)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.render(value), encoding="utf-8", newline="\n")


class TestRound10(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.answers = self.work / "answers"
        self.packs = self.work / "blind_packs"
        self.private = self.work / "private.json"
        self.submissions = self.work / "submissions"
        self.seal = run.seal_path(self.private)
        self.study = run.load_study()
        self.actual_readers = self.work / "actual-readers.json"
        save(self.actual_readers, self.study.protocol["blind_readers"])

    def seed_answers(self, payments=True):
        for arm, qids in (("candidate", self.study.cases), ("bare", self.study.subset), ("payment", self.study.payment_cases if payments else [])):
            markers = {"candidate": "雨桐标记", "bare": "碎镜标记", "payment": "砂台标记"}
            for qid in qids:
                target = {"极速": 68, "标准": 320, "深度": 850}[self.study.rules.get(qid, {}).get("tier", "标准")]
                marker = markers[arm]
                text = marker * (target // len(marker))
                if arm == "candidate" and qid == "Q16":
                    text = "雨" * 272 + "7" * 78
                path = self.answers / arm / f"{qid}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((text + "\r\n").encode("utf-8-sig"))

    def seed_packs(self, archive=False, payments=True):
        self.seed_answers(payments)
        self.counts = run.pack(self.study, self.answers, self.packs, self.private, run.ARCHIVE if archive else None)["counts"]
        self.meta = run.parse(self.private.read_bytes())["assignments"]

    def body(self, meta):
        return run.parse(run.pack_file(self.packs, meta).read_bytes())

    def write_stream(self, stream, rows):
        save(self.submissions / stream / "batch.json", rows)

    def seed_submissions(self, close_reader="J3"):
        rows = {name: [] for name in run.STREAMS}
        recall_i = Counter()
        win_i = 0
        majority = {"Q03", "Q09", "Q15", "Q01", "Q06", "Q08", "Q10", "Q12"}
        close_wins = {"Q03", "Q09", "Q15", "Q01", "Q06"}
        recall_bits = [[1, 0, 0], [0, 1, 0], [0, 0, 1]] * 3 + [[1, 1, 0], [1, 0, 1], [0, 1, 1]]
        for meta in self.meta:
            key, kind = meta["assignment_id"], meta["kind"]
            row = {"assignment_id": key}
            body = self.body(meta)
            if kind == "recall":
                arm = meta["arm"]
                i = recall_i[arm]
                recall_i[arm] += 1
                bits = [1, 1, 1] if i < 24 else recall_bits[i - 24] if arm == "candidate" else [0, int(i < 29), 0]
                rows[kind].append({**row, **{key: "复述所读信息" for key in run.FIELDS}})
                rows["recall_grades"].append({
                    **row, "scores": dict(zip(run.FIELDS, bits)),
                    "quotes": {f: body["answer"][:4] for f, score in zip(run.FIELDS, bits) if score},
                    "cross_field": ["limitation"] if not bits[1] else [],
                })
            elif kind == "preference":
                qid, reader, side = meta["qid"], meta["reader"], meta["candidate_position"]
                win = qid in (close_wins if reader == close_reader else majority)
                choice = side if win else "B" if side == "A" else "A"
                if reader == close_reader and qid in ("Q14", "Q30"):
                    choice = "tie" if qid == "Q14" else "none"
                quotes = body["answers"][choice][:4] if choice in ("A", "B") else ""
                rows[kind].append({**row, "choice": choice, "quote": quotes, "reason": "有决策条件", "friction": "另一答没提限期"})
                if choice in ("A", "B"):
                    status = "grounded" if not win or win_i < 17 else "unsubstantiated" if win_i < 20 else "depends_on_error"
                    rows["reason_checks"].append({**row, "status": status, "basis": "合成核验记录"})
                win_i += win
            elif kind == "rubric":
                qid = meta["qid"]
                rule = self.study.rules[qid]
                rows[kind].append({
                    **row, "observed_mode": rule["mode"], "observed_cards": rule["cards"],
                    "condition_hits": list(self.study.baseline[qid]["condition_hits"]),
                    "effect_hits": [1] * len(rule.get("effects", [])), "vetoes": [], "quotes": [],
                })
            else:
                answer = body["items"][0]["answer"] if kind == "control" else body["answer"]
                rows["controls" if kind == "control" else kind].append({**row, "pass": True, "quotes": [answer[:4]], "basis": "合成评分依据"})
        for name, records in rows.items():
            if records:
                self.write_stream(name, records)
        return rows

    def validate(self, seal=False, incomplete=False, actual=True):
        return run.validate(self.study, self.answers, self.packs, self.private, self.submissions,
                            seal=seal, seal_incomplete=incomplete, actual_readers=self.actual_readers if actual else None)

    def reveal(self, actual=True):
        return run.reveal(self.study, self.answers, self.packs, self.private, self.submissions,
                          actual_readers=self.actual_readers if actual else None)

    def test_freeze_canonical_lf_and_frozen_inputs(self):
        protocol_path = self.work / "tests/eval_round10/protocol.json"
        save(protocol_path, self.study.protocol)
        for spec in self.study.protocol["frozen_inputs"].values():
            path = self.work / spec["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            data = (run.ROOT / spec["path"]).read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))
        test_study = run.load_study(self.work)
        self.assertEqual(test_study.cases, self.study.cases)
        snapshot = run.freeze_skill(self.study)
        self.assertEqual(snapshot["manuals"]["count"], 15)
        self.assertGreater(snapshot["runtime"]["count"], 15)
        self.assertIn("scripts/lint_report.py", snapshot["runtime"]["files"])
        skill_dir = self.work / "test_skill"
        skill_dir.mkdir()
        file = skill_dir / "normal.md"
        file.write_bytes(b"text\r\n")
        expected = run.digest(f"normal.md\t{run.digest(b'text' + bytes([10]))}\n".encode("utf-8"))
        self.assertEqual(run.directory_fingerprint(skill_dir, [file])["sha256_lf"], expected)
        (self.work / self.study.protocol["frozen_inputs"]["cases"]["path"]).write_bytes(b"[]")
        with self.assertRaisesRegex(ValueError, "冻结输入哈希不符"):
            run.load_study(self.work)

    def test_pack_isolated_and_balanced(self):
        self.seed_packs(archive=True, payments=False)
        self.assertEqual(run.parse(self.private.read_bytes())["slot_layout"], "v2_shuffled")
        self.assertEqual(self.counts, {"recall": 72, "preference": 36, "rubric": 76, "control": 6})
        self.assertEqual(len(list((self.packs / "rubric").glob("*.json"))), 82)
        family = Counter()
        question = Counter()
        ids = set()
        for meta in self.meta:
            body = self.body(meta)
            aid = body.pop("assignment_id")
            self.assertRegex(aid, r"^[0-9a-f]{32}$")
            self.assertNotIn(aid, ids)
            ids.add(aid)
            self.assertEqual(run.pack_file(self.packs, meta).stem, aid)
            blacklist = r"(?i)\b(?:candidate|bare|Q\d{2}|arm|qid|reader|pack_sha256|private_map|candidate_position|slot_layout)\b"
            self.assertIsNone(re.search(blacklist, json.dumps(body, ensure_ascii=False)))
            if meta["kind"] == "recall":
                self.assertEqual(body.keys(), {"answer", "questions"})
                self.assertEqual(body["questions"], run.QUESTIONS)
                self.assertTrue(body["answer"].endswith("\r\n"))
            if meta["kind"] == "preference":
                qid, reader, side = meta["qid"], meta["reader"], meta["candidate_position"]
                family[reader, side] += 1
                question[qid, side] += 1
                self.assertEqual(body.keys(), {"prompt", "answers"})
                self.assertEqual(body["prompt"], self.study.cases[qid])
                self.assertEqual(body["answers"].keys(), {"A", "B"})
                marker = "雨" if qid == "Q16" else "雨桐标记"
                self.assertTrue(body["answers"][side].startswith(marker))
                if qid == "Q09":
                    self.assertEqual([e["id"] for e in body["prompt"]["evidence"]], ["E1", "E2", "E3", "E4"])
                    self.assertEqual(body["prompt"]["evidence"][3]["source"], "合成材料：新区装置采办清单检索卡")
        for reader in self.study.protocol["blind_readers"]:
            self.assertEqual([family[reader, side] for side in "AB"], [6, 6])
        for qid in self.study.subset:
            self.assertEqual(sorted(question[qid, side] for side in "AB"), [1, 2])
        self.assertFalse(self.private.is_relative_to(self.packs))
        legacy = importlib.util.spec_from_file_location("r8_blind", run.ROOT / "tests/eval_round8/blind.py")
        assert legacy is not None and legacy.loader is not None
        blind = importlib.util.module_from_spec(legacy)
        legacy.loader.exec_module(blind)
        old = next(meta for meta in self.meta if meta["kind"] == "rubric" and meta["arm"] == "r8" and meta["qid"] == "Q03")
        self.assertEqual(self.body(old)["answer"].replace("\r\n", "\n").strip(), blind.samples("Q03")["C"])

    def test_v2_shuffles_each_recall_list_and_breaks_fixed_alternation(self):
        self.seed_packs()
        orders = set()
        for reader in self.study.protocol["blind_readers"]:
            recall = tuple(
                meta["qid"] for meta in self.meta if meta["kind"] == "recall" and meta["reader"] == reader
            )
            self.assertEqual(Counter(recall), dict.fromkeys(self.study.subset, 2))
            self.assertTrue(all(a != b for a, b in zip(recall, recall[1:])))
            orders.add(recall)
            sides = "".join(meta["candidate_position"] for meta in self.meta if meta["kind"] == "preference" and meta["reader"] == reader)
            self.assertNotIn(sides, ("AB" * 6, "BA" * 6))
        self.assertEqual(len(orders), len(self.study.protocol["blind_readers"]))

        second_map = self.work / "second.json"
        run.pack(self.study, self.answers, self.work / "second_packs", second_map)
        second = run.parse(second_map.read_bytes())["assignments"]
        self.assertEqual(
            [{k: v for k, v in meta.items() if k not in ("assignment_id", "pack_sha256")} for meta in self.meta],
            [{k: v for k, v in meta.items() if k not in ("assignment_id", "pack_sha256")} for meta in second],
        )

    def test_legacy_v1_map_and_seal_replay_with_newer_runtime(self):
        self.seed_answers()
        run.pack(self.study, self.answers, self.packs, self.private, slot_layout="v1")
        private = run.parse(self.private.read_bytes())
        self.assertEqual(private.pop("slot_layout"), "v1")
        save(self.private, private)
        self.meta = private["assignments"]
        for reader in self.study.protocol["blind_readers"]:
            qids = [meta["qid"] for meta in self.meta if meta["kind"] == "recall" and meta["reader"] == reader]
            self.assertEqual(qids[::2], qids[1::2])
        self.seed_submissions()
        self.assertTrue(self.validate(seal=True, actual=False)["sealed"])
        self.assertNotIn("actual_readers_sha256", run.parse(self.seal.read_bytes()))
        with self.assertRaisesRegex(ValueError, "封存未绑定"):
            self.reveal()

        drifted = {**private["snapshot"], "runtime": {}}
        with patch.object(run, "freeze_skill", return_value=drifted):
            with self.assertRaisesRegex(ValueError, "运行文件或协议已变"):
                self.validate(actual=False)
            report = self.reveal(actual=False)
        self.assertEqual(report["slot_layout"], "v1")
        self.assertTrue(report["gates"]["C"]["by_slot_planned"]["pass"])
        self.assertFalse(report["gates"]["C"]["pass"])
        self.assertIsNone(report["gates"]["C"]["checks"]["winning_families"]["actual"])
        self.assertIn("异构家族门未验证", run.markdown(report))

    def test_pack_refuses_missing_answer_and_public_mapping(self):
        self.seed_answers()
        with self.assertRaisesRegex(ValueError, "私有映射"):
            run.pack(self.study, self.answers, self.packs, self.packs / "private.json")
        (self.answers / "candidate/Q02.md").unlink()
        with self.assertRaisesRegex(ValueError, "candidate/Q02 缺首答"):
            run.pack(self.study, self.answers, self.packs, self.private)
        self.assertFalse(self.packs.exists())
        for header in ("# Candidate", "# Q02", "# R8 Candidate Answer - Q02", "我是裸模型答卷"):
            path = self.answers / "candidate/Q02.md"
            path.write_text(header + "\n不许改写这个首答。", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "显式臂标/题号"):
                run.pack(self.study, self.answers, self.packs, self.private)
            self.assertTrue(path.read_text(encoding="utf-8").startswith(header))

    def test_pack_does_not_claim_preregistered_without_both_prompt_hashes(self):
        self.seed_answers()
        snapshot = self.work / "freeze.json"
        save(snapshot, run.freeze_skill(self.study))
        with self.assertRaisesRegex(ValueError, "预注册快照缺两臂提示哈希"):
            run.pack(self.study, self.answers, self.packs, self.private, frozen=snapshot)

    def test_validate_detects_package_and_original_answer_changes(self):
        self.seed_packs()
        meta = next(row for row in self.meta if row["kind"] == "preference" and row["qid"] == "Q09")
        path = run.pack_file(self.packs, meta)
        initial = path.read_bytes()
        body = self.body(meta)
        body["prompt"]["evidence"][3]["source"] = "被简写的标题"
        body["arm"] = "candidate"
        save(path, body)
        with self.assertRaisesRegex(ValueError, "盲包内容变更或含非公开字段"):
            self.validate()
        path.write_bytes(initial)
        answer = self.answers / "candidate/Q09.md"
        answer.write_bytes(answer.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "首答字节已变"):
            self.validate()

    def test_validate_rejects_missing_duplicate_invalid_value_and_wrong_quote(self):
        self.seed_packs()
        rows = self.seed_submissions()["preference"]
        self.write_stream("preference", rows[1:])
        invalid = self.validate(seal=True)
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["missing"]["preference"], [rows[0]["assignment_id"]])
        self.assertFalse(self.seal.exists())
        for replacements, error in (
            (rows + [rows[0]], "重复 assignment_id"),
            ([{**rows[0], "choice": "candidate"}, *rows[1:]], "choice 必须"),
            ([{**rows[0], "quote": rows[0]["quote"][0]}, *rows[1:]], "至少需 4 个可见字符"),
            ([{**rows[0], "quote": "不属于任一原答的证据标题"}, *rows[1:]], "引句未出现"),
            ([{**rows[0], "qid": "Q09"}, *rows[1:]], "字段必须为"),
        ):
            with self.subTest(error=error):
                self.write_stream("preference", replacements)
                state = self.validate()
                self.assertFalse(state["ok"])
                self.assertIn(error, " ".join(state["errors"]))
        path = self.submissions / "preference/batch.json"
        path.write_text(run.render(rows).replace('"choice":', '"choice":"A", "choice":', 1), encoding="utf-8")
        self.assertIn("JSON 重复字段", " ".join(self.validate()["errors"]))

    def test_quote_minimum_ignores_spacing_and_invisible_characters(self):
        for quote in ("的", "的 \n \t", "的\u200b\u200b\u200b"):
            with self.subTest(quote=quote), self.assertRaisesRegex(ValueError, "至少需 4 个可见字符"):
                run.check_quote(quote, [quote + "是原答的一部分。"])
        run.check_quote("场 地\n规 则", ["以场 地\r\n规 则为准。"])

    def test_reveal_computes_frozen_gates_and_preserves_raw_votes(self):
        self.seed_packs(archive=True)
        records = self.seed_submissions()
        self.assertTrue(self.validate(seal=True)["ok"])
        report = self.reveal()
        self.assertTrue(report["all_three_pass"], report["gates"])
        self.assertTrue(report["payment"]["pass"])
        a, b, c = (report["gates"][key]["checks"] for key in "ABC")
        self.assertEqual([a[name]["actual"] for name in ("vetoes", "modes", "cards", "conditions", "controls", "q09_effects", "han", "visible")], [0, 32, 20, 100, 6, 4, 31, 32])
        self.assertEqual([b[name]["actual"] for name in ("submissions", "total", "limitation", "all_correct")], [72, 87, 29, 24])
        self.assertEqual(report["recall"]["cross_field"]["candidate"]["limitation"], 7)
        self.assertEqual([c[name]["actual"] for name in ("wins", "net", "two_reader_wins", "new_other_text_two_reader_wins", "winning_families", "effective_net")], [21, 8, 8, 5, 2, 4])
        raw = report["preference"]["counts"]["overall"]["all"]
        self.assertEqual(raw, {"W": 21, "L": 13, "T": 1, "none": 1, "missing": 0})
        self.assertEqual(report["preference"]["weighted"]["net"], 7.5)
        self.assertEqual(report["preference"]["reason_checks"]["W"], {"grounded": 17, "unsubstantiated": 3, "depends_on_error": 1, "missing": 0})
        self.assertEqual([vote["original"] for vote in report["preference"]["entries"]], records["preference"])
        for arm in ("candidate", "r8"):
            q16 = report["lengths"][arm]["by_id"]["Q16"]
            self.assertEqual((q16["han"], q16["visible"], q16["han_pass"], q16["visible_pass"]), (272, 350, False, True))
        self.assertEqual(run.count_han("㐀一𠀀𯨟　1㏠"), 4)
        self.assertIn("Q16 | 标准 | 272（未通过） | 350（通过）", run.markdown(report))

    def test_actual_generator_family_win_cannot_pass_heterogeneous_gate(self):
        self.seed_packs()
        self.seed_submissions(close_reader="J2")
        readers = {**self.study.protocol["blind_readers"],
                   "J3": {"agent": "omp", "model": "congming/gpt-6.2-astra", "family": "OpenAI"}}
        save(self.actual_readers, readers)
        self.assertTrue(self.validate(seal=True)["sealed"])
        report = self.reveal()
        c = report["gates"]["C"]
        tallies = report["preference"]["counts"]
        self.assertTrue(c["by_slot_planned"]["pass"])
        self.assertEqual(c["by_slot_planned"]["checks"]["winning_families"]["actual"], 2)
        self.assertEqual(tallies["by_slot_planned"]["DeepSeek"], tallies["by_actual_family"]["OpenAI"])
        self.assertGreater(tallies["by_actual_family"]["OpenAI"]["W"], tallies["by_actual_family"]["OpenAI"]["L"])
        self.assertEqual(set(tallies["strict_non_generator_families"]), {"Google", "xAI"})
        self.assertEqual(c["checks"]["winning_families"], run.metric(1, 2, 2))
        self.assertFalse(c["pass"] or report["all_three_pass"])
        md = run.markdown(report)
        self.assertIn("| by_actual_family | OpenAI | 8 | 4 |", md)
        self.assertIn("与生成者同族、不计入异构门：OpenAI", md)
        self.assertNotIn("DeepSeek", md)

    def test_actual_readers_require_exact_sealed_bytes(self):
        self.seed_packs()
        self.seed_submissions()
        self.assertTrue(self.validate(seal=True)["sealed"])
        raw = self.actual_readers.read_bytes()
        self.assertEqual(run.parse(self.seal.read_bytes())["actual_readers_sha256"], run.digest(raw))
        with self.assertRaisesRegex(ValueError, "缺少 --actual-readers"):
            self.reveal(actual=False)
        self.actual_readers.write_bytes(raw + b"\n")
        with self.assertRaisesRegex(ValueError, "实际通道文件已变"):
            self.reveal()

    def test_actual_readers_reject_extra_fields_before_seal(self):
        self.seed_packs()
        self.seed_submissions()
        readers = {**self.study.protocol["blind_readers"],
                   "J3": {**self.study.protocol["blind_readers"]["J3"], "api_key": "不应接收的字段"}}
        save(self.actual_readers, readers)
        with self.assertRaisesRegex(ValueError, "字段必须为：agent model family"):
            self.validate(seal=True)
        self.assertFalse(self.seal.exists())

    def test_reveal_incomplete_skip_never_becomes_tie_or_rewrites_vote(self):
        self.seed_packs(payments=False)
        rows = self.seed_submissions()
        vote = rows["preference"][0]
        vote.update(choice="skip", quote="", reason="", friction="")
        self.write_stream("preference", rows["preference"])
        self.write_stream("reason_checks", [record for record in rows["reason_checks"] if record["assignment_id"] != vote["assignment_id"]])
        state = self.validate(seal=True, incomplete=True)
        self.assertEqual(state["missing"]["preference_skipped"], [vote["assignment_id"]])
        self.assertTrue(state["sealed"])
        self.assertFalse(state["ok"])
        report = self.reveal()
        self.assertTrue(report["gates"]["A"]["pass"] and report["gates"]["B"]["pass"])
        self.assertFalse(report["gates"]["C"]["pass"])
        self.assertEqual(report["preference"]["counts"]["overall"]["all"]["T"], 1)
        self.assertEqual(report["preference"]["counts"]["overall"]["all"]["missing"], 1)
        self.assertEqual(report["payment"]["missing"], ["N2", "N5-equivalent"])
        original = self.submissions / "preference/batch.json"
        original.write_bytes(original.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "提交已变"):
            self.reveal()
        with self.assertRaisesRegex(ValueError, "输出已存在"):
            self.validate(seal=True, incomplete=True)
        moved_map = self.work / "moved_private.json"
        moved_map.write_bytes(self.private.read_bytes())
        with self.assertRaisesRegex(ValueError, "映射已移动"):
            run.validate(self.study, self.answers, self.packs, moved_map, self.submissions, seal=True, seal_incomplete=True)

    def test_reveal_missing_bare_grade_is_diagnostic_only(self):
        self.seed_packs()
        rows = self.seed_submissions()
        key = next(meta["assignment_id"] for meta in self.meta if meta["kind"] == "rubric" and meta["arm"] == "bare")
        self.write_stream("rubric", [row for row in rows["rubric"] if row["assignment_id"] != key])
        self.assertFalse(self.validate(seal=True, incomplete=True)["ok"])
        report = self.reveal()
        self.assertFalse(report["all_three_pass"])
        self.assertEqual(report["validation"]["missing"]["rubric"], [key])

    def test_validate_cannot_score_explicitly_unreadable_recall(self):
        self.seed_packs()
        rows = self.seed_submissions()
        rows["recall"][0]["conclusion"] = "读不出来。\n"
        self.write_stream("recall", rows["recall"])
        state = self.validate()
        self.assertFalse(state["ok"])
        self.assertIn("读不出来不可得分", " ".join(state["errors"]))

    def test_recall_gate_fails_if_candidate_limitation_trails_bare(self):
        self.seed_packs()
        rows = self.seed_submissions()
        bare = {meta["assignment_id"]: meta for meta in self.meta if meta["kind"] == "recall" and meta["arm"] == "bare"}
        grade = next(row for row in rows["recall_grades"] if row["assignment_id"] in bare and not row["scores"]["limitation"])
        grade["scores"]["limitation"] = 1
        grade["quotes"]["limitation"] = self.body(bare[grade["assignment_id"]])["answer"][:4]
        grade["cross_field"].remove("limitation")
        self.write_stream("recall_grades", rows["recall_grades"])
        self.assertTrue(self.validate(seal=True)["ok"])
        gates = self.reveal()["gates"]
        self.assertTrue(gates["A"]["pass"] and gates["C"]["pass"])
        self.assertTrue(gates["B"]["checks"]["not_below_bare_total"]["pass"])
        self.assertFalse(gates["B"]["checks"]["not_below_bare_limitation"]["pass"])
        self.assertFalse(gates["B"]["pass"])

    def test_confirmed_regression_cannot_be_offset_by_another_condition(self):
        self.seed_packs()
        rows = self.seed_submissions()
        by_qid = {meta["qid"]: meta for meta in self.meta if meta["kind"] == "rubric" and meta["arm"] == "candidate"}
        grades = {row["assignment_id"]: row for row in rows["rubric"]}
        drop = grades[by_qid["Q01"]["assignment_id"]]
        drop["condition_hits"][0] = 0
        grades[by_qid["Q06"]["assignment_id"]]["condition_hits"][2] = 1
        self.write_stream("rubric", rows["rubric"])
        self.assertEqual(self.validate()["missing"]["rubric_reviews"], [drop["assignment_id"]])
        self.write_stream("rubric_reviews", [{**drop, "quotes": [self.body(by_qid["Q01"])["answer"][:4]]}])
        self.assertTrue(self.validate(seal=True)["ok"])
        checks = self.reveal()["gates"]["A"]["checks"]
        self.assertTrue(checks["conditions"]["pass"])
        self.assertFalse(checks["confirmed_regressions"]["pass"])

    def test_cli_freeze_pack_validate_reveal(self):
        self.seed_answers(payments=False)
        freeze = self.work / "freeze.json"
        opts = ["--answers", str(self.answers), "--packs", str(self.packs), "--map-out", str(self.private)]

        def cli(*args):
            proc = subprocess.run([sys.executable, "-B", run.__file__, *args], capture_output=True, encoding="utf-8", check=False)
            data = json.loads(proc.stdout)
            self.assertEqual(proc.returncode, 0, (data, proc.stderr))
            return data

        candidate_prompt, bare_prompt = self.work / "full.md", self.work / "plain.md"
        candidate_prompt.write_text("候选提示", encoding="utf-8")
        bare_prompt.write_text("对照提示", encoding="utf-8")
        self.assertEqual(cli("freeze-skill", "--out", str(freeze), "--prompts", str(candidate_prompt), str(bare_prompt))["manuals"]["count"], 15)
        self.assertTrue(cli("pack", *opts, "--frozen", str(freeze))["pre_registered_snapshot"])
        self.meta = run.parse(self.private.read_bytes())["assignments"]
        self.seed_submissions()
        opts += ["--submissions", str(self.submissions), "--actual-readers", str(self.actual_readers)]
        self.assertTrue(cli("validate", *opts, "--seal")["sealed"])
        md, result = self.work / "report.md", self.work / "report.json"
        data = cli("reveal", *opts, "--json-out", str(result), "--markdown-out", str(md))
        self.assertTrue(data["all_three_pass"])
        self.assertFalse(data["payment_pass"])
        self.assertIn("主持人私有", md.read_text(encoding="utf-8"))
        self.assertTrue(run.parse(result.read_bytes())["all_three_pass"])


if __name__ == "__main__":
    unittest.main()
