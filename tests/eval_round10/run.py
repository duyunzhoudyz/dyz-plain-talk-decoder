"""R10 主持人本地工具。只转交单份盲包；不要转交 CLI、私有映射、封存或揭盲报告。

首答路径：answers/{candidate,bare}/Qxx.md，可选 answers/payment/{N2,N5-equivalent}.md。
提交目录内各文件是 JSON 对象数组（字段见 --help），按匿名 assignment_id 接收，不改答、不改票。
评分及复核由隔离评分者完成，本脚本不根据词面替代语义判断，不调用模型或联网。
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import random
import re
import sys
from typing import Any
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / ".tmp/orch/r10"
ARCHIVE = ROOT / "tests/eval_round8/answers/candidate"
FIELDS = ("conclusion", "limitation", "next_step")
QUESTIONS = dict(zip(FIELDS, ("结论是什么？", "结论受什么条件限制？", "下一步具体做什么？")))
CHOICES = ("A", "B", "tie", "none", "skip")
REASONS = ("grounded", "unsubstantiated", "depends_on_error")
SLOT_LAYOUTS = ("v1", "v2_shuffled")
TIERS = {"极速": "fast", "标准": "standard", "深度": "deep"}
STREAMS = {
    "recall": "recall", "recall_grades": "recall", "preference": "preference",
    "reason_checks": "preference", "rubric": "rubric", "rubric_reviews": "rubric",
    "controls": "control", "payment": "payment",
}
JSON = dict[str, Any]
Answers = dict[str, dict[str, tuple[str, str]]]
Slots = list[tuple[JSON, JSON]]
EXPLICIT_ID = re.compile(
    r"(?i)(?<![a-z0-9])(?:candidate|bare|Q(?:0[1-9]|[12]\d|3[0-2]))(?![a-z0-9])|"
    r"候选(?:臂|组首答|首答|答卷)|裸模(?:型)?(?:臂|组|首答|答卷)?|存档(?:臂|首答)"
)


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def lf_digest(data: bytes) -> str:
    return digest(data.replace(b"\r\n", b"\n"))


def unique_keys(pairs: list[tuple[str, Any]]) -> JSON:
    row: JSON = {}
    for key, value in pairs:
        if key in row:
            raise ValueError(f"JSON 重复字段：{key}")
        row[key] = value
    return row


def parse(data: bytes) -> Any:
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_keys)


def render(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def output_path(path: Path, packs: Path | None = None) -> Path:
    resolved = path.resolve()
    if any(resolved.is_relative_to(ROOT / f"tests/eval_round{n}") for n in (8, 9)):
        raise ValueError("不能写入冻结的 R8/R9 目录")
    if packs is not None and resolved.is_relative_to(packs.resolve()):
        raise ValueError("私有映射、封存和报告不能写入盲包目录")
    if resolved.exists() or path.is_symlink():
        raise ValueError(f"输出已存在，不能覆盖：{path}")
    return resolved


def seal_path(map_path: Path) -> Path:
    return map_path.resolve().with_suffix(".seal.json")


def seal_location_digest(map_path: Path) -> str:
    return digest(str(seal_path(map_path)).encode("utf-8"))


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(text)


def prompt(case: JSON) -> JSON:
    # 只投影本题完整字段；不要把题号、分组或同题其他臂带入盲包。
    return {key: case[key] for key in ("request", "text", "evidence", "profile") if key in case}


@dataclass(frozen=True)
class Study:
    root: Path
    protocol: JSON
    protocol_hash: str
    cases: dict[str, JSON]
    rubric: JSON
    baseline: dict[str, JSON]
    payment_cases: dict[str, JSON]

    @property
    def subset(self) -> list[str]:
        return self.protocol["anchors"] + self.protocol["new"]

    @property
    def rules(self) -> dict[str, JSON]:
        return self.rubric["by_id"]


def load_study(root: Path = ROOT) -> Study:
    data = (root / "tests/eval_round10/protocol.json").read_bytes()
    protocol = parse(data)
    inputs = {}
    for name, item in protocol["frozen_inputs"].items():
        original = (root / item["path"]).read_bytes()
        if lf_digest(original) != item["sha256_lf"]:
            raise ValueError(f"冻结输入哈希不符：{item['path']}")
        inputs[name] = parse(original)
    cases = {case["id"]: prompt(case) for case in inputs["cases"]}
    payment_cases = {
        qid: item["prompt"] if "prompt" in item else inputs["payment_source"]["cases"][item["source_case"]]
        for qid, item in protocol["payment"].items()
    }
    return Study(root, protocol, lf_digest(data), cases, inputs["rubric"], inputs["baseline_grades"]["by_id"], payment_cases)


def directory_fingerprint(skill_dir: Path, paths: list[Path]) -> JSON:
    files = {path.relative_to(skill_dir).as_posix(): lf_digest(path.read_bytes()) for path in paths}
    manifest = "".join(f"{name}\t{files[name]}\n" for name in sorted(files))
    return {"count": len(files), "sha256_lf": digest(manifest.encode("utf-8")), "files": files}


def protocol_snapshot(study: Study) -> JSON:
    return {"round": study.protocol["round"], "protocol_sha256_lf": study.protocol_hash,
            "frozen_inputs": study.protocol["frozen_inputs"]}


def freeze_skill(study: Study, prompt_files: tuple[Path, Path] | None = None) -> JSON:
    skill = study.root / "dyz-plain-talk-decoder"
    paths = sorted(path for path in skill.rglob("*") if path.is_file() and path.suffix in {".md", ".py", ".txt"})
    manuals = [path for path in paths if path.suffix == ".md"]
    if len(manuals) != 15:
        raise ValueError(f"运行包应有 15 份 Markdown，实际 {len(manuals)} 份")
    snapshot = {
        **protocol_snapshot(study),
        "runtime": directory_fingerprint(skill, paths), "manuals": directory_fingerprint(skill, manuals),
    }
    if prompt_files is not None:
        snapshot["prompts_sha256_lf"] = {arm: lf_digest(path.read_bytes()) for arm, path in zip(("candidate", "bare"), prompt_files)}
    return snapshot


def load_answers(study: Study, source: Path, historical: Path | None) -> Answers:
    sources = {"candidate": (source / "candidate", study.cases), "bare": (source / "bare", study.subset)}
    if historical is not None:
        sources["r8"] = historical, study.cases
    if (source / "payment").is_dir():
        sources["payment"] = source / "payment", [qid for qid in study.payment_cases if (source / "payment" / f"{qid}.md").is_file()]
    answers: Answers = {}
    issues: list[str] = []
    for arm, (directory, qids) in sources.items():
        answers[arm] = {}
        extra = sorted({path.stem for path in directory.glob("*.md")} - set(qids))
        if extra:
            issues.append(f"{arm} 非预注册首答：{', '.join(extra)}")
        for qid in qids:
            path = directory / f"{qid}.md"
            if not path.is_file():
                issues.append(f"{arm}/{qid} 缺首答")
                continue
            data = path.read_bytes()
            text = data.decode("utf-8-sig")
            if not text.strip() or EXPLICIT_ID.search(text):
                issues.append(f"{arm}/{qid} 首答为空或含显式臂标/题号；须人工处理，脚本不会剪改原答")
                continue
            answers[arm][qid] = text, digest(data)
    if issues:
        raise ValueError("；".join(issues))
    return answers


def answer_hashes(answers: Answers) -> JSON:
    return {arm: {qid: pair[1] for qid, pair in rows.items()} for arm, rows in answers.items()}


def make_slots(study: Study, answers: Answers, slot_layout: str) -> Slots:
    require(slot_layout in SLOT_LAYOUTS, "未知盲包布局 slot_layout")
    slots: Slots = []
    rng = random.Random(study.protocol["seed"])
    anchors, new = list(study.protocol["anchors"]), list(study.protocol["new"])
    rng.shuffle(anchors)
    rng.shuffle(new)
    qids = anchors + new
    positions = []
    if slot_layout == "v2_shuffled":
        require(len(study.protocol["blind_readers"]) == 3 and len(qids) == len(set(qids)) == 12,
                "v2_shuffled 布局要求三席与 12 道不同题目")
        # 每列恰为 2:1；六种列各重复两次，每席自然各有六次 A、B。
        positions = ["AAB", "ABA", "BAA", "ABB", "BAB", "BBA"] * 2
        while True:
            rng.shuffle(positions)
            if all(any(a == b for a, b in zip(order, order[1:])) for order in zip(*positions)):
                break
    for j, reader in enumerate(study.protocol["blind_readers"]):
        recall: Slots = []
        for i, qid in enumerate(qids):
            side = positions[i][j] if positions else "AB"[(i + j) % 2]
            arms = ("candidate", "bare") if side == "A" else ("bare", "candidate")
            for arm in arms:
                (recall if positions else slots).append((
                    {"kind": "recall", "reader": reader, "qid": qid, "arm": arm},
                    {"answer": answers[arm][qid][0], "questions": QUESTIONS},
                ))
            slots.append((
                {"kind": "preference", "reader": reader, "qid": qid, "candidate_position": side},
                {"prompt": study.cases[qid], "answers": dict(zip("AB", (answers[arm][qid][0] for arm in arms)))},
            ))
        if recall:
            while True:
                rng.shuffle(recall)
                if all(a[0]["qid"] != b[0]["qid"] for a, b in zip(recall, recall[1:])):
                    break
            slots.extend(recall)
    rubric_order = [(arm, qid) for arm in ("candidate", "bare", "r8") for qid in answers.get(arm, {})]
    rng.shuffle(rubric_order)
    for arm, qid in rubric_order:
        slots.append((
            {"kind": "rubric", "arm": arm, "qid": qid},
            {"prompt": study.cases[qid], "answer": answers[arm][qid][0],
             "rubric": {key: value for key, value in study.rules[qid].items() if key != "reader"},
             "global_veto": study.rubric["scoring"]["global_veto"]},
        ))
    for group, item in enumerate(study.rubric["cross_case_pairs"], 1):
        criteria = item["pass"]
        for i, qid in enumerate(item["ids"], 1):
            criteria = criteria.replace(qid, f"第{i}份")
        slots.append((
            {"kind": "control", "group": group},
            {"items": [{"prompt": study.cases[qid], "answer": answers["candidate"][qid][0]} for qid in item["ids"]],
             "rubric": {"axis": item["axis"], "constant": item["constant"], "pass": criteria}},
        ))
    for qid, pair in answers.get("payment", {}).items():
        slots.append((
            {"kind": "payment", "qid": qid},
            {"prompt": study.payment_cases[qid], "answer": pair[0], "rubric": study.protocol["payment"][qid]["criteria"]},
        ))
    return slots


def pack_file(packs: Path, meta: JSON) -> Path:
    kind = meta["kind"] if meta["kind"] in ("recall", "preference") else "rubric"
    return packs / kind / f"{meta['assignment_id']}.json"


def pack(study: Study, source: Path, packs: Path, map_out: Path, historical: Path | None = None,
         frozen: Path | None = None, *, slot_layout: str = "v2_shuffled") -> JSON:
    packs = output_path(packs)
    map_out = output_path(map_out, packs)
    if packs.is_relative_to(map_out) or any(
        out.is_relative_to(directory.resolve()) for out in (packs, map_out)
        for directory in (source, historical) if directory is not None
    ):
        raise ValueError("盲包和映射不能写入首答目录或彼此内部")
    snapshot = freeze_skill(study)
    if frozen is not None:
        preregistered = parse(frozen.read_bytes())
        if any(preregistered.get(key) != value for key, value in snapshot.items()):
            raise ValueError("当前运行文件或协议与冻结快照不符")
        hashes = preregistered.get("prompts_sha256_lf", {})
        if not isinstance(hashes, dict) or hashes.keys() != {"candidate", "bare"} or not all(
            isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes.values()
        ):
            raise ValueError("预注册快照缺两臂提示哈希，请在生成前用 freeze-skill --prompts 登记")
        snapshot = preregistered
    answers = load_answers(study, source, historical)
    files: dict[Path, str] = {}
    assignments = []
    for slot, payload in make_slots(study, answers, slot_layout):
        assignment = uuid4().hex
        body = render({"assignment_id": assignment, **payload})
        meta = {"assignment_id": assignment, **slot, "pack_sha256": digest(body.encode("utf-8"))}
        files[pack_file(packs, meta)] = body
        assignments.append(meta)
    packs.mkdir(parents=True)
    for path in sorted(files):
        write_new(path, files[path])
    write_new(map_out, render({
        "snapshot": snapshot, "pre_registered_snapshot": frozen is not None, "seal_location_sha256": seal_location_digest(map_out),
        "slot_layout": slot_layout, "historical": historical is not None,
        "answers_sha256": answer_hashes(answers), "assignments": assignments,
    }))
    return {"ok": True, "counts": dict(Counter(meta["kind"] for meta in assignments)),
            "pre_registered_snapshot": frozen is not None, "slot_layout": slot_layout}


def check_packs(study: Study, source: Path, packs: Path, map_path: Path, historical: Path | None, *,
                sealed_map_hash: str | None = None) -> tuple[JSON, dict[str, tuple[JSON, JSON]], Answers, str]:
    if map_path.resolve().is_relative_to(packs.resolve()):
        raise ValueError("私有映射位于盲包目录，不能继续")
    raw_map = map_path.read_bytes()
    map_hash = digest(raw_map)
    private = parse(raw_map)
    if private["seal_location_sha256"] != seal_location_digest(map_path):
        raise ValueError("私有映射已移动，不能另行封存同一批次")
    # 封存绑定当时的运行包指纹；揭盲复算不要求把当前 Skill 回滚到生成版本。
    if sealed_map_hash is not None:
        require(map_hash == sealed_map_hash, "映射已变，或与封存记录不符")
    expected_snapshot = freeze_skill(study) if sealed_map_hash is None else protocol_snapshot(study)
    if any(private["snapshot"].get(key) != value for key, value in expected_snapshot.items()):
        raise ValueError("运行文件或协议已变，不能继续使用这一批盲包")
    if historical is not None and not private["historical"]:
        raise ValueError("这一批没有历史臂")
    answers = load_answers(study, source, (historical or ARCHIVE) if private["historical"] else None)
    if private["answers_sha256"] != answer_hashes(answers):
        raise ValueError("首答字节已变，或历史对照路径与打包时不符")
    slots = make_slots(study, answers, private.get("slot_layout", "v1"))
    if len(slots) != len(private["assignments"]):
        raise ValueError("私有映射分母不符")
    index = {}
    expected_paths = set()
    mismatches = []
    for meta, (expected, payload) in zip(private["assignments"], slots):
        key = meta["assignment_id"]
        if not re.fullmatch(r"[a-f0-9]{32}", key) or key in index:
            raise ValueError("私有映射含重复或不透明格式以外的编号")
        if meta != {**expected, "assignment_id": key, "pack_sha256": meta.get("pack_sha256")}:
            raise ValueError("私有映射与预注册分题、家族或位置轮换不符")
        path = pack_file(packs, meta)
        expected_paths.add(path)
        body = {"assignment_id": key, **payload}
        if not path.is_file() or path.is_symlink():
            mismatches.append(f"{key} 缺包或不是普通文件")
        else:
            raw = path.read_bytes()
            if digest(raw) != meta["pack_sha256"] or parse(raw) != body:
                mismatches.append(f"{key} 盲包内容变更或含非公开字段")
        index[key] = meta, body
    extras = [path.relative_to(packs).as_posix() for path in packs.rglob("*")
              if (not path.is_dir() or path.is_symlink()) and path not in expected_paths]
    if extras or mismatches:
        raise ValueError("；".join(mismatches + [f"盲包目录包含额外文件：{name}" for name in sorted(extras)]))
    return private, index, answers, map_hash


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def exact(row: Any, fields: str) -> None:
    require(isinstance(row, dict) and row.keys() == set(fields.split()), f"字段必须为：{fields}")


def read_actual_readers(study: Study, path: Path) -> tuple[JSON, str]:
    raw = path.read_bytes()
    readers = parse(raw)
    exact(readers, " ".join(study.protocol["blind_readers"]))
    for row in readers.values():
        exact(row, "agent model family")
        require(all(nonempty(value) and re.fullmatch(r"[\w./:+() -]{1,128}", value) for value in row.values()),
                "实际通道只接受 agent/model/family 名称，不得填写密钥或其他信息")
    return readers, digest(raw)


def members(values: Any, allowed: Any) -> bool:
    return isinstance(values, list) and all(isinstance(v, str) and v in allowed for v in values) and len(set(values)) == len(values)


def bits(values: Any, size: int) -> bool:
    return isinstance(values, list) and len(values) == size and all(type(v) is int and v in (0, 1) for v in values)


def check_quote(quote: Any, texts: list[str]) -> None:
    require(isinstance(quote, str) and sum(ch.isprintable() and not ch.isspace() for ch in quote) >= 4,
            "引句去除空白后至少需 4 个可见字符")
    require(any(quote.replace("\r\n", "\n") in text.replace("\r\n", "\n") for text in texts),
            "引句未出现在指定原答（需逐字引用）")


def check_quotes(quotes: Any, texts: list[str], required: bool) -> None:
    require(isinstance(quotes, list) and (bool(quotes) or not required), "quotes 缺少原句依据或不是数组")
    for quote in quotes:
        check_quote(quote, texts)
    require(len(quotes) == len(set(quotes)), "quotes 含重复引句")


def check_row(stream: str, row: JSON, body: JSON, rows: dict[str, dict[str, JSON]]) -> None:
    key = row["assignment_id"]
    if stream == "recall":
        exact(row, "assignment_id conclusion limitation next_step")
        require(all(nonempty(row[field]) for field in FIELDS), "复述字段不可空；可写读不出来")
    elif stream == "preference":
        exact(row, "assignment_id choice quote reason friction")
        choice = row["choice"]
        require(choice in CHOICES, "choice 必须是 A/B/tie/none/skip")
        require(all(isinstance(row[field], str) for field in ("quote", "reason", "friction")), "quote/reason/friction 必须是字符串")
        require(choice == "skip" or all(nonempty(row[field]) for field in ("reason", "friction")), "非跳过票须填写理由及比较")
        if choice in ("A", "B") or row["quote"]:
            check_quote(row["quote"], [body["answers"][choice]] if choice in ("A", "B") else list(body["answers"].values()))
    elif stream in ("controls", "payment"):
        exact(row, "assignment_id pass quotes basis")
        require(type(row["pass"]) is bool and nonempty(row["basis"]), "pass 必须为布尔值且须说明依据")
        texts = [item["answer"] for item in body["items"]] if stream == "controls" else [body["answer"]]
        check_quotes(row["quotes"], texts, required=True)
    elif stream in ("rubric", "rubric_reviews"):
        exact(row, "assignment_id observed_mode observed_cards condition_hits effect_hits vetoes quotes")
        rule = body["rubric"]
        require(row["observed_mode"] is None or row["observed_mode"] in ("M1", "M2", "M3", "M4", "M5"), "非法主模式")
        require(members(row["observed_cards"], ("产品", "消费", "招聘", "财报", "研数")), "非法或重复场景卡")
        for field, source in (("condition_hits", "conditions"), ("effect_hits", "effects")):
            require(bits(row[field], len(rule.get(source, []))), f"{field} 应为按量表原序的 0/1 数组")
        vetoes = [f"{scope}:{i}" for scope, items in (("global", body["global_veto"]), ("case", rule["hard_veto"]))
                  for i in range(1, len(items) + 1)]
        require(members(row["vetoes"], vetoes), "硬否决编号应为量表有效的 global:N 或 case:N，且不得重复")
        check_quotes(row["quotes"], [body["answer"]], required=bool(row["vetoes"]) or stream == "rubric_reviews")
    elif stream == "recall_grades":
        exact(row, "assignment_id scores quotes cross_field")
        exact(row["scores"], " ".join(FIELDS))
        require(bits(list(row["scores"].values()), 3), "scores 仅收 0 或 1")
        recall = rows["recall"].get(key)
        require(recall is not None, "缺复述原提交，不能评分")
        require(members(row["cross_field"], FIELDS) and all(not row["scores"][f] for f in row["cross_field"]),
                "cross_field 仅记录未在本栏得分的字段，不能补分")
        quotes = row["quotes"]
        require(isinstance(quotes, dict) and quotes.keys() <= set(FIELDS), "quotes 须以复述字段为键")
        for field in FIELDS:
            if row["scores"][field]:
                require(recall[field].strip().strip("。！!.").strip() != "读不出来", "读不出来不可得分")
                require(field in quotes, f"{field} 得分缺答复原句")
        for quote in quotes.values():
            check_quote(quote, [body["answer"]])
    elif stream == "reason_checks":
        exact(row, "assignment_id status basis")
        require(row["status"] in REASONS and nonempty(row["basis"]), "理由核验必须用规定状态并说明依据")
        vote = rows["preference"].get(key)
        require(vote is not None and vote["choice"] in ("A", "B"), "理由核验只对应已有独胜原票")


def regressions(study: Study, meta: JSON, grade: JSON) -> list[int]:
    if meta["arm"] not in ("candidate", "r8"):
        return []
    before = study.baseline[meta["qid"]]["condition_hits"]
    return [i for i, (old, new) in enumerate(zip(before, grade["condition_hits"]), 1) if old and not new]


def collect(study: Study, index: dict[str, tuple[JSON, JSON]], directory: Path) -> tuple[dict[str, dict[str, JSON]], JSON, JSON]:
    rows: dict[str, dict[str, JSON]] = {stream: {} for stream in STREAMS}
    errors = []
    hashes = {}
    paths = set(directory.rglob("*.json"))
    for stream, kind in STREAMS.items():
        seen = set()
        for path in sorted((directory / stream).glob("*.json")):
            paths.discard(path)
            name = path.relative_to(directory).as_posix()
            try:
                raw = path.read_bytes()
                records = parse(raw)
                require(isinstance(records, list), "提交必须为 JSON 对象数组")
                hashes[name] = digest(raw)
            except (OSError, ValueError) as exc:
                errors.append(f"{name}：{exc}")
                continue
            for i, row in enumerate(records, 1):
                try:
                    require(isinstance(row, dict), "记录必须为对象")
                    key = row.get("assignment_id")
                    require(isinstance(key, str) and key in index and index[key][0]["kind"] == kind, "编号未知或提交类型不符")
                    require(key not in seen, "重复 assignment_id")
                    seen.add(key)
                    check_row(stream, row, index[key][1], rows)
                    rows[stream][key] = row
                except ValueError as exc:
                    errors.append(f"{name} 第 {i} 条：{exc}")
    errors.extend(f"未知提交文件：{path.relative_to(directory).as_posix()}" for path in sorted(paths))
    missing = {}
    for stream, kind in STREAMS.items():
        expected = {key for key, (meta, _) in index.items() if meta["kind"] == kind}
        if stream == "reason_checks":
            expected = {key for key, vote in rows["preference"].items() if vote["choice"] in ("A", "B")}
        elif stream == "rubric_reviews":
            expected = {key for key, grade in rows["rubric"].items() if grade["vetoes"] or regressions(study, index[key][0], grade)}
        if expected - rows[stream].keys():
            missing[stream] = sorted(expected - rows[stream].keys())
    skipped = sorted(key for key, vote in rows["preference"].items() if vote["choice"] == "skip")
    if skipped:
        missing["preference_skipped"] = skipped
    unmeasured_payment = sorted(study.payment_cases.keys() - {meta["qid"] for meta, _ in index.values() if meta["kind"] == "payment"})
    state = {"ok": not errors and not missing, "errors": errors, "missing": missing,
             "unmeasured_payment": unmeasured_payment, "counts": {name: len(data) for name, data in rows.items()}}
    return rows, state, hashes


def validate(study: Study, source: Path, packs: Path, map_path: Path, submissions: Path, historical: Path | None = None,
             seal: bool = False, seal_incomplete: bool = False, *, actual_readers: Path | None = None) -> JSON:
    _, index, _, map_hash = check_packs(study, source, packs, map_path, historical)
    reader_hash = read_actual_readers(study, actual_readers)[1] if actual_readers is not None else None
    _, state, hashes = collect(study, index, submissions)
    if seal and not state["errors"] and (state["ok"] or seal_incomplete):
        path = output_path(seal_path(map_path), packs)
        require(not path.is_relative_to(submissions.resolve()), "封存记录需放在提交目录外")
        record = {"map_sha256": map_hash, "submission_sha256": hashes, "complete": state["ok"]}
        if reader_hash is not None:
            record["actual_readers_sha256"] = reader_hash
        write_new(path, render(record))
        state["sealed"] = True
    return state


def count_han(text: str) -> int:
    return sum(0x3400 <= ord(ch) <= 0x4DBF or 0x4E00 <= ord(ch) <= 0x9FFF or 0x20000 <= ord(ch) <= 0x2FA1F for ch in text)


def lengths(study: Study, answers: Answers) -> tuple[JSON, JSON]:
    spec = importlib.util.spec_from_file_location("r10_lint", study.root / "dyz-plain-talk-decoder/scripts/lint_report.py")
    assert spec is not None and spec.loader is not None
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    result = {}
    ledger = {}
    for arm in ("candidate", "bare", "r8"):
        if arm not in answers:
            continue
        by_id = {}
        for qid, (answer, _) in answers[arm].items():
            tier = study.rules[qid]["tier"]
            low, high = study.rubric["scoring"]["length"][tier]
            visible, _ = lint.count_visible_chars(answer)
            han = count_han(answer)
            by_id[qid] = {"tier": tier, "bounds": [low, high], "han": han, "visible": visible,
                          "han_pass": low <= han <= high, "visible_pass": low <= visible <= high}
            if arm == "candidate":
                gate = lint.lint_report(answer, tier=TIERS[tier], offline=True, high_risk=qid in study.rubric["coverage"]["high_stakes"])
                if gate["errors"] or gate["warnings"]:
                    ledger[qid] = {key: gate[key] for key in ("errors", "warnings")}
        by_tier = {}
        for tier in TIERS:
            subset = [row for row in by_id.values() if row["tier"] == tier]
            by_tier[tier] = {"total": len(subset), **{field: sum(row[f"{field}_pass"] for row in subset) for field in ("han", "visible")}}
        result[arm] = {"by_id": by_id, "by_tier": by_tier, "total": len(by_id),
                       **{field: sum(row[f"{field}_pass"] for row in by_id.values()) for field in ("han", "visible")}}
    return result, ledger


def metric(actual: int, target: int, total: int | None = None, op: str = ">=") -> JSON:
    passed = {">=": actual >= target, "<=": actual <= target, "=": actual == target, ">": actual > target}[op]
    return {"actual": actual, "target": target, "op": op, "total": total, "pass": passed}


def gate(checks: JSON) -> JSON:
    return {"pass": all(check["pass"] for check in checks.values()), "checks": checks}


def rubric_report(study: Study, index: dict[str, tuple[JSON, JSON]], rows: dict[str, dict[str, JSON]], length: JSON) -> tuple[JSON, JSON, JSON]:
    a = study.protocol["thresholds"]["A"]
    detail: JSON = {"candidate": {}, "bare": {}, "r8": {}}
    control = {}
    payment = dict.fromkeys(study.payment_cases)
    scores: dict[str, Counter] = {arm: Counter() for arm in detail}
    for key, (meta, _) in index.items():
        kind = meta["kind"]
        if kind == "control":
            control[meta["group"]] = rows["controls"].get(key)
        if kind == "payment":
            payment[meta["qid"]] = rows["payment"].get(key)
        if kind != "rubric":
            continue
        arm, qid = meta["arm"], meta["qid"]
        primary, review = rows["rubric"].get(key), rows["rubric_reviews"].get(key)
        detail[arm][qid] = {"primary": primary, "review": review}
        if primary is None:
            continue
        rule = study.rules[qid]
        drops = regressions(study, meta, primary)
        confirmed = [i for i in drops if review is not None and not review["condition_hits"][i - 1]]
        detail[arm][qid].update(regression_candidates=drops, confirmed_regressions=confirmed)
        total = scores[arm]
        total["graded"] += 1
        total["vetoes"] += bool(primary["vetoes"] or (review and review["vetoes"]))
        total["review_missing"] += bool((primary["vetoes"] or drops) and review is None)
        total["regressions"] += len(confirmed)
        total["modes"] += primary["observed_mode"] == rule["mode"]
        total["cards"] += rule["mode"] == "M2" and set(primary["observed_cards"]) == set(rule["cards"])
        total["conditions"] += sum(primary["condition_hits"])
        total["q09_effects"] += sum(primary["effect_hits"]) if qid == "Q09" else 0
    r = scores["candidate"]
    checks = {
        "answers": metric(length["total"], a["answers"], a["answers"], "="),
        "graded": metric(r["graded"], a["answers"], a["answers"], "="),
        "vetoes": metric(r["vetoes"], a["max_vetoes"], a["answers"], "<="),
        "modes": metric(r["modes"], a["modes"], a["modes"]),
        "cards": metric(r["cards"], a["cards"], a["cards"]),
        "conditions": metric(r["conditions"], *a["conditions"]),
        "controls": metric(sum(bool(row and row["pass"]) for row in control.values()), a["controls"], a["controls"]),
        "q09_effects": metric(r["q09_effects"], a["q09_effects"], a["q09_effects"]),
        "han": metric(length["han"], a["han"], a["answers"]),
        "visible": metric(length["visible"], a["visible"], a["answers"]),
        "review_missing": metric(r["review_missing"], 0, op="="),
        "confirmed_regressions": metric(r["regressions"], a["max_confirmed_regressions"], op="<="),
        **{f"han_{TIERS[tier]}": metric(length["by_tier"][tier]["han"], *target) for tier, target in a["han_by_tier"].items()},
    }
    p = study.protocol["thresholds"]["payment"]
    supplemental = metric(sum(bool(row and row["pass"]) for row in payment.values()), p["pass"], p["total"])
    supplemental.update(by_id=payment, missing=[qid for qid, row in payment.items() if row is None])
    return gate(checks), {"by_arm": scores, "by_id": detail, "controls": control}, supplemental


def recall_report(study: Study, index: dict[str, tuple[JSON, JSON]], rows: dict[str, dict[str, JSON]]) -> tuple[JSON, JSON]:
    b = study.protocol["thresholds"]["B"]
    arms: dict[str, Counter] = {name: Counter({key: 0 for key in (*FIELDS, "total", "all_correct", "graded")}) for name in ("candidate", "bare")}
    cross = {name: Counter() for name in arms}
    detail = []
    for key, (meta, _) in index.items():
        if meta["kind"] != "recall":
            continue
        arm = meta["arm"]
        grade = rows["recall_grades"].get(key)
        detail.append({**{field: meta[field] for field in ("qid", "reader", "arm")}, "recall": rows["recall"].get(key), "grade": grade})
        if grade is None:
            continue
        arms[arm].update(grade["scores"])
        score = sum(grade["scores"].values())
        arms[arm]["total"] += score
        arms[arm]["all_correct"] += score == len(FIELDS)
        arms[arm]["graded"] += 1
        cross[arm].update(grade["cross_field"])
    cand = arms["candidate"]
    checks = {"submissions": metric(len(rows["recall"]), b["submissions"], b["submissions"], "="),
              "grades": metric(len(rows["recall_grades"]), b["submissions"], b["submissions"], "="),
              "total": metric(cand["total"], *b["total"]), "all_correct": metric(cand["all_correct"], *b["all_correct"]),
              **{field: metric(cand[field], *b["per_field"]) for field in FIELDS},
              **{f"not_below_bare_{field}": metric(cand[field], arms["bare"][field]) for field in b["not_below_bare"]}}
    return gate(checks), {"by_arm": arms, "cross_field": cross, "entries": detail}


def preference_report(study: Study, index: dict[str, tuple[JSON, JSON]], rows: dict[str, dict[str, JSON]],
                      actual_readers: JSON | None = None) -> tuple[JSON, JSON]:
    c = study.protocol["thresholds"]["C"]
    readers = study.protocol["blind_readers"]
    cluster = set(study.protocol["half_weight_cluster"])
    empty_tally = dict.fromkeys(("W", "L", "T", "none", "missing"), 0)
    counts = {axis: {name: Counter(empty_tally) for name in names} for axis, names in (
        ("overall", ["all"]), ("by_question", study.subset), ("by_slot_planned", [r["family"] for r in readers.values()]),
        ("by_group", ["anchors", "new"]), ("by_position", ["A", "B"]), ("same_text_cluster", ["half_weight", "other"]),
    )}
    family_names: dict[str, str] = {}
    actual_families = {}
    for reader, row in (actual_readers or {}).items():
        family = row["family"].strip()
        actual_families[reader] = family_names.setdefault(family.casefold(), family)
    actual_counts = {family: Counter(empty_tally) for family in family_names.values()}
    reasons = {outcome: Counter(dict.fromkeys((*REASONS, "missing"), 0)) for outcome in ("W", "L")}
    detail = []
    for key, (meta, _) in index.items():
        if meta["kind"] != "preference":
            continue
        reader, qid, position = meta["reader"], meta["qid"], meta["candidate_position"]
        family = readers[reader]["family"]
        vote, review = rows["preference"].get(key), rows["reason_checks"].get(key)
        choice = vote["choice"] if vote else "skip"
        outcome = ("W" if choice == position else "L") if choice in ("A", "B") else {"tie": "T", "none": "none", "skip": "missing"}[choice]
        groups = ("all", qid, family, "anchors" if qid in study.protocol["anchors"] else "new", position, "half_weight" if qid in cluster else "other")
        for axis, name in zip(counts.values(), groups):
            axis[name][outcome] += 1
        if outcome in ("W", "L"):
            reasons[outcome][review["status"] if review else "missing"] += 1
        entry = {"qid": qid, "reader": reader, "family_planned": family, "candidate_position": position,
                 "outcome": outcome, "original": vote, "reason_check": review}
        if actual_readers is not None:
            entry["actual_family"] = actual_families[reader]
            actual_counts[actual_families[reader]][outcome] += 1
        detail.append(entry)
    totals = counts["overall"]["all"]
    wins, losses = totals["W"], totals["L"]
    two_wins = {qid for qid, tally in counts["by_question"].items() if tally["W"] >= 2}
    effective_net = reasons["W"]["grounded"] - reasons["L"]["grounded"]
    grounded_percent = 100 * reasons["W"]["grounded"] / wins if wins else None
    wanted_percent = c["grounded_candidate_win_percent"]
    checks = {
        "submissions": metric(c["submissions"] - totals["missing"], c["submissions"], c["submissions"], "="),
        "wins": metric(wins, c["wins"], c["submissions"]), "net": metric(wins - losses, c["net"]),
        "two_reader_wins": metric(len(two_wins), *c["two_reader_wins"]),
        "new_other_text_two_reader_wins": metric(len(two_wins.intersection(set(study.protocol["new"]) - cluster)), *c["new_other_text_two_reader_wins"]),
        "winning_families": metric(sum(tally["W"] > tally["L"] for tally in counts["by_slot_planned"].values()), c["winning_families"], len(readers)),
        "reason_checks": metric(reasons["W"]["missing"] + reasons["L"]["missing"], 0, op="="),
        "grounded_candidate_win_percent": {"actual": grounded_percent, "target": wanted_percent, "op": ">=", "total": None,
                                           "pass": wins > 0 and 100 * reasons["W"]["grounded"] >= wins * wanted_percent},
        "effective_net": metric(effective_net, c["effective_net_min_exclusive"], op=">"),
    }
    planned_gate = gate(checks.copy())
    if actual_readers is None:
        checks["winning_families"] = {"actual": None, "target": c["winning_families"], "op": ">=", "total": None, "pass": False}
    else:
        generator = study.protocol["generator"]["family"].strip().casefold()
        counts["by_actual_family"] = actual_counts
        strict = counts["strict_non_generator_families"] = {
            family: tally for family, tally in actual_counts.items() if family.casefold() != generator
        }
        checks["winning_families"] = metric(sum(tally["W"] > tally["L"] for tally in strict.values()), c["winning_families"], len(strict))
    weighted = {key: counts["same_text_cluster"]["half_weight"][key] / 2 + counts["same_text_cluster"]["other"][key] for key in totals}
    weighted["net"] = weighted["W"] - weighted["L"]
    return {**gate(checks), "by_slot_planned": planned_gate}, {
        "counts": counts, "weighted": weighted, "reason_checks": reasons, "entries": detail, "actual_readers": actual_readers,
    }


def reveal(study: Study, source: Path, packs: Path, map_path: Path, submissions: Path, historical: Path | None = None,
           *, actual_readers: Path | None = None) -> JSON:
    seal = seal_path(map_path)
    require(not seal.resolve().is_relative_to(packs.resolve()), "封存记录不能放在盲包目录")
    sealed = parse(seal.read_bytes())
    require(isinstance(sealed, dict) and isinstance(sealed.get("map_sha256"), str), "封存格式错误，缺映射哈希")
    private, index, answers, map_hash = check_packs(study, source, packs, map_path, historical, sealed_map_hash=sealed["map_sha256"])
    require((actual_readers is not None) == ("actual_readers_sha256" in sealed),
            "实际通道须与封存记录一致：缺少 --actual-readers，或封存未绑定该文件")
    readers = None
    if actual_readers is not None:
        readers, reader_hash = read_actual_readers(study, actual_readers)
        require(reader_hash == sealed["actual_readers_sha256"], "实际通道文件已变，或与封存记录不符")
    rows, state, hashes = collect(study, index, submissions)
    require(not state["errors"], "存在非法提交，不能揭盲：" + "；".join(state["errors"]))
    expected = {"map_sha256": map_hash, "submission_sha256": hashes, "complete": state["ok"]}
    if actual_readers is not None:
        expected["actual_readers_sha256"] = reader_hash
    require(sealed == expected,
            "映射或提交已变，或与封存记录不符")
    length, ledger = lengths(study, answers)
    a, grade, payment = rubric_report(study, index, rows, length["candidate"])
    b, recall = recall_report(study, index, rows)
    c, preference = preference_report(study, index, rows, readers)
    return {
        "round": study.protocol["round"], "scope": "AI 离线盲读；不代表真人、全平台验收或发布放行",
        "slot_layout": private.get("slot_layout", "v1"),
        "pre_registered_snapshot": private["pre_registered_snapshot"], "historical_rescore_included": private["historical"],
        "validation": state, "gates": {"A": a, "B": b, "C": c},
        "all_three_pass": not (state["missing"].keys() - {"payment"}) and all(x["pass"] for x in (a, b, c)),
        "lengths": length, "lint_ledger": ledger, "lint_ledger_scope": "当前脚本自检，不改写封存首答或评分",
        "rubric": grade, "recall": recall, "preference": preference, "payment": payment,
    }


def markdown(report: JSON) -> str:
    def result(ok: bool) -> str:
        return "通过" if ok else "未通过"

    lines = ["# R10 揭盲统计（主持人私有）", "", report["scope"], "",
             f"- 三门合计：{result(report['all_three_pass'])}；" + "、".join(f"门 {name} {result(row['pass'])}" for name, row in report["gates"].items()),
             f"- 已绑定生成前快照：{report['pre_registered_snapshot']}；有 R8 存档重评：{report['historical_rescore_included']}",
             f"- 付款专项：{report['payment']['actual']}/{report['payment']['total']}，{result(report['payment']['pass'])}",
             "", "| 门 | 判项 | 实测 | 阈值 | 结果 |", "|---|---|---:|---:|---|"]
    for name, gate_report in report["gates"].items():
        for item, row in gate_report["checks"].items():
            actual = row["actual"] if row["actual"] is not None else "未测"
            shown = f"{actual}/{row['total']}" if row["total"] is not None else str(actual)
            lines.append(f"| {name} | {item} | {shown} | {row['op']} {row['target']} | {result(row['pass'])} |")
    counts = report["preference"]["counts"]
    planned = report["gates"]["C"]["by_slot_planned"]["checks"]["winning_families"]
    lines.extend(["", f"计划槽位（by_slot_planned）净胜 {planned['actual']}/{planned['total']}；仅供对照，不计入实际异构家族门。"])
    if "by_actual_family" in counts:
        strict = counts["strict_non_generator_families"]
        excluded = counts["by_actual_family"].keys() - strict.keys()
        lines.append(f"实到非生成者家族：{'、'.join(strict) or '无'}；与生成者同族、不计入异构门：{'、'.join(sorted(excluded)) or '无'}。")
    else:
        lines.append("未封存实际通道，计划槽位不代表实际参评家族，异构家族门未验证。")
    lines.extend(["", "## 双口径篇幅", "", "| 臂 | 档位 | 汉字达标 | 可见字符达标 |", "|---|---|---:|---:|"])
    for arm, value in report["lengths"].items():
        for tier, row in value["by_tier"].items():
            lines.append(f"| {arm} | {tier} | {row['han']}/{row['total']} | {row['visible']}/{row['total']} |")
    lines.extend(["", "| 候选题号 | 档位 | R8 汉字 | 门禁可见字符 |", "|---|---|---:|---:|"])
    for qid, row in report["lengths"]["candidate"]["by_id"].items():
        lines.append(f"| {qid} | {row['tier']} | {row['han']}（{result(row['han_pass'])}） | {row['visible']}（{result(row['visible_pass'])}） |")
    lines.extend(["", "## 偏好原票（相对候选）", "", "| 维度 | 分组 | W | L | T | 都不用 | 缺测 |", "|---|---|---:|---:|---:|---:|---:|"])
    for axis, groups in counts.items():
        if axis == "by_slot_planned" and "by_actual_family" in counts:
            continue
        for name, tally in groups.items():
            lines.append(f"| {axis} | {name} | {tally['W']} | {tally['L']} | {tally['T']} | {tally['none']} | {tally['missing']} |")
    weighted = report["preference"]["weighted"]
    lines.extend(["", f"Q03/Q30 各半权：W={weighted['W']:g}，L={weighted['L']:g}，净票 {weighted['net']:g}。", "",
                  "| 原票方向 | 有据 | 未获支持 | 依赖错误 | 缺核验 |", "|---|---:|---:|---:|---:|"])
    for outcome, row in report["preference"]["reason_checks"].items():
        lines.append(f"| {outcome} | {row['grounded']} | {row['unsubstantiated']} | {row['depends_on_error']} | {row['missing']} |")
    missing = [f"{stream} {len(ids)}" for stream, ids in report["validation"]["missing"].items()]
    lines.extend(["", f"- 缺测提交：{'、'.join(missing) if missing else '无'}。",
                  f"- 付款缺测：{'、'.join(report['payment']['missing']) or '无'}。",
                  "- 完整 JSON 保留逐题复核、原票、理由、缺测编号及词面门禁台账。", ""])
    return "\n".join(lines)


SUBMISSION_HELP = """提交目录下每类一个子目录，每个 .json 文件放匿名记录数组，不能重复编号。
recall: assignment_id, conclusion, limitation, next_step（读不出来就这样写）。
recall_grades: assignment_id, scores（对应三字段的 0/1 对象）, quotes（字段到答复原句）, cross_field（答在其他栏的字段数组）。
preference: assignment_id, choice（A/B/tie/none/skip）, quote（独胜须来自被选答复）, reason, friction。
reason_checks: assignment_id, status（grounded/unsubstantiated/depends_on_error）, basis（仅独胜原票）。
rubric / rubric_reviews: assignment_id, observed_mode（M1–M5 或 null）, observed_cards（量表卡名数组）, condition_hits 和 effect_hits（0/1 数组）, vetoes（global:N 或 case:N，N 从 1 开始）, quotes（答复原句数组）。
controls / payment: assignment_id, pass（布尔值）, quotes（答复原句数组）, basis。
复述评分者单独接收一份复述、对应原答、题面和量表 reader 项；理由核验者单独接收一份偏好包及原票。都不可见臂名、映射与历史结果。
"""


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = cli.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze-skill", help="核对冻结输入并登记运行包指纹")
    freeze.add_argument("--out", type=Path, help="私有快照路径；不指定则仅打印 JSON")
    freeze.add_argument("--prompts", type=Path, nargs=2, metavar=("CANDIDATE", "BARE"), help="生成前登记两臂提示的 LF 哈希")
    for command, help_text in (("pack", "只从封存首答生成单份盲包"), ("validate", "校验首答、盲包及提交，可封存"), ("reveal", "仅在封存后揭盲并汇总三门")):
        sub = commands.add_parser(command, help=help_text, epilog=SUBMISSION_HELP if command != "pack" else None,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
        sub.add_argument("--answers", type=Path, default=HERE / "answers", help="含 candidate/bare、可选 payment 的目录（仅虚构材料与合成画像）")
        sub.add_argument("--packs", type=Path, default=WORK / "blind_packs", help="盲包目录（默认 .tmp/orch/r10/blind_packs）")
        sub.add_argument("--map-out", type=Path, default=WORK.parent / "r10_private_map.json", help="盲包目录外的私有映射路径（pack 写入，其余读取）")
        sub.add_argument("--historical", type=Path, nargs="?", const=ARCHIVE, help="可选 R8 candidate 首答目录；无参数值时取仓库存档")
        if command == "pack":
            sub.add_argument("--frozen", type=Path, help="生成首答前由 freeze-skill 保存的快照（正式轮应指定）")
            sub.add_argument("--slot-layout", choices=SLOT_LAYOUTS, default="v2_shuffled", help="盲包布局，默认 v2_shuffled")
        else:
            sub.add_argument("--submissions", type=Path, default=WORK / "submissions", help="已收集的匿名 JSON 提交目录")
            sub.add_argument("--actual-readers", type=Path,
                             help="私有 JSON：J1/J2/J3 各填 agent/model/family 名称，不含密钥；封存和揭盲须使用同一文件内容")
        if command == "validate":
            sub.add_argument("--seal", action="store_true", help="校验通过后在私有映射旁写唯一的 .seal.json，不可重封")
            sub.add_argument("--seal-incomplete", action="store_true", help="配合 --seal：只有缺测、无非法记录时也封存，仅做诊断揭盲")
        if command == "reveal":
            sub.add_argument("--json-out", type=Path, help="输出完整 JSON（默认标准输出）")
            sub.add_argument("--markdown-out", type=Path, help="另存 Markdown 摘要")
    return cli


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        study = load_study()
        if args.command == "freeze-skill":
            data = freeze_skill(study, tuple(args.prompts) if args.prompts else None)
            if args.out is not None:
                write_new(output_path(args.out, WORK / "blind_packs"), render(data))
        elif args.command == "pack":
            data = pack(study, args.answers, args.packs, args.map_out, args.historical, args.frozen, slot_layout=args.slot_layout)
        elif args.command == "validate":
            data = validate(study, args.answers, args.packs, args.map_out, args.submissions, args.historical, args.seal, args.seal_incomplete,
                            actual_readers=args.actual_readers)
        else:
            json_out = output_path(args.json_out, args.packs) if args.json_out else None
            md_out = output_path(args.markdown_out, args.packs) if args.markdown_out else None
            require(json_out is None or json_out != md_out, "JSON 和 Markdown 必须使用不同路径")
            require(not any(path and path.is_relative_to(args.submissions.resolve()) for path in (json_out, md_out)), "报告不能写入提交目录")
            data = reveal(study, args.answers, args.packs, args.map_out, args.submissions, args.historical, actual_readers=args.actual_readers)
            if md_out is not None:
                write_new(md_out, markdown(data))
            if json_out is not None:
                write_new(json_out, render(data))
                data = {"ok": True, "all_three_pass": data["all_three_pass"], "payment_pass": data["payment"]["pass"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        data = {"ok": False, "errors": [str(exc)]}
    print(render(data), end="")
    return 0 if data.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
