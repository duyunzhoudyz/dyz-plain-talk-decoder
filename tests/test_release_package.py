"""发布包自包含、版本同步与 PEP 723 元数据校验（离线，不联网）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 兼容
    tomllib = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "dyz-plain-talk-decoder"
SKILL_MD = SKILL_DIR / "SKILL.md"
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"

FORBIDDEN_NAMES = {
    ".env",
    "profile.md",
    "pytest.ini",
    "plain_talk_decoder_skill_prd.md",
    "AGENTS.md",
    "CLAUDE.md",
}
FORBIDDEN_DIR_PARTS = {".pytest_cache", ".venv", "tests", ".git"}
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}\b"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+"),
    re.compile(r"/Users/[^/\s]+/"),
]


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    assert match, "SKILL.md 缺少 YAML frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip().strip('"').strip("'")
    return fields


def _extract_pep723_block(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "# /// script"), -1)
    assert start != -1, f"{path.name} 缺少 PEP 723 '# /// script' 声明"
    end = next((i for i in range(start + 1, len(lines)) if line_is_end(lines[i])), -1)
    assert end != -1, f"{path.name} 的 PEP 723 声明未闭合"
    body = []
    for raw in lines[start + 1 : end]:
        assert raw.startswith("#"), f"{path.name} PEP 723 块内必须以 '#' 开头：{raw!r}"
        body.append(raw[2:] if raw.startswith("# ") else raw[1:])
    return "\n".join(body)


def line_is_end(line: str) -> bool:
    return line.strip() == "# ///"


class TestReleasePackage:
    def test_skill_frontmatter_and_line_budget(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) <= 500, f"SKILL.md 超过 500 行红线（当前 {len(lines)} 行）"
        fm = _parse_frontmatter(text)
        assert fm.get("name") == "dyz-plain-talk-decoder"
        assert re.fullmatch(r"\d+\.\d+\.\d+", fm.get("version", "")), f"非法版本号：{fm.get('version')!r}"
        assert fm.get("description") and "不用于" in fm["description"]

    def test_plugin_manifests_sync_with_skill(self):
        fm = _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))

        assert plugin["name"] == fm["name"]
        assert plugin["version"] == fm["version"]
        assert plugin["license"] == "MIT"
        for rel in plugin["skills"]:
            target = (ROOT / rel).resolve()
            assert (target / "SKILL.md").is_file(), f"plugin.json 引用的技能目录不存在：{rel}"

        assert marketplace["name"] == fm["name"]
        assert any(p["name"] == fm["name"] for p in marketplace["plugins"])

    def test_all_referenced_manuals_exist(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        refs = sorted(set(re.findall(r"references/[a-zA-Z0-9_./-]+\.md", text)))
        assert refs, "SKILL.md 未引用任何 references 手册"
        for rel in refs:
            assert (SKILL_DIR / rel).is_file(), f"SKILL.md 引用的手册不存在：{rel}"

    def test_skill_directory_is_self_contained_and_clean(self):
        for path in SKILL_DIR.rglob("*"):
            rel_parts = set(path.relative_to(SKILL_DIR).parts)
            if "__pycache__" in rel_parts:
                continue  # 本地非 -B 跑 pytest 时 Python 自动生成，已在 .gitignore 排除
            assert not (rel_parts & FORBIDDEN_DIR_PARTS), f"分发目录混入非运行目录：{path}"
            if path.is_file():
                assert path.name not in FORBIDDEN_NAMES, f"分发目录混入开发/隐私文件：{path}"
                assert not path.name.startswith("test_"), f"分发目录混入测试文件：{path}"
                assert path.suffix not in {".pyc", ".pyo", ".tmp"}, f"分发目录混入临时文件：{path}"

    def test_no_secrets_or_personal_paths_in_release_files(self):
        checked = list(SKILL_DIR.rglob("*.md")) + list(SKILL_DIR.rglob("*.py")) + [
            PLUGIN_JSON,
            MARKETPLACE_JSON,
            ROOT / "README.md",
        ]
        for path in checked:
            content = path.read_text(encoding="utf-8")
            for pat in SECRET_PATTERNS:
                assert not pat.search(content), f"{path.relative_to(ROOT)} 疑似包含密钥或个人路径"

    def test_pep723_headers_valid_and_aligned_with_requirements(self):
        req_lines = [
            line.strip()
            for line in (SKILL_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        fetch_toml = _extract_pep723_block(SKILL_DIR / "scripts" / "fetch_content.py")
        env_toml = _extract_pep723_block(SKILL_DIR / "scripts" / "check_env.py")
        clean_toml = _extract_pep723_block(SKILL_DIR / "scripts" / "cleanup_cache.py")
        lint_toml = _extract_pep723_block(SKILL_DIR / "scripts" / "lint_report.py")
        card_toml = _extract_pep723_block(SKILL_DIR / "scripts" / "render_card.py")

        if tomllib is not None:
            fetch_meta = tomllib.loads(fetch_toml)
            env_meta = tomllib.loads(env_toml)
            clean_meta = tomllib.loads(clean_toml)
            lint_meta = tomllib.loads(lint_toml)
            card_meta = tomllib.loads(card_toml)
            assert fetch_meta["requires-python"] == ">=3.10"
            assert fetch_meta["dependencies"] == req_lines
            assert env_meta["dependencies"] == []
            assert clean_meta["dependencies"] == []
            assert lint_meta["dependencies"] == []
            assert card_meta["dependencies"] == []
        else:  # Python 3.10 回退文本校验
            for req in req_lines:
                assert req in fetch_toml
            assert "dependencies = []" in env_toml
            assert "dependencies = []" in clean_toml
            assert "dependencies = []" in lint_toml
            assert "dependencies = []" in card_toml
