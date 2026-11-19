"""真实网络冒烟测试（P2-10）。默认不运行，需要手动触发：

    .venv/Scripts/python.exe -m pytest tests/test_live.py -m live -s

要测的链接写在 tests/live_urls.txt：每行 `类型 链接`，类型为 web 或 wechat，`#` 开头是注释。
运行前会访问这些真实地址，所以链接清单必须先给用户过目。
每个链接只请求一次（失败也不重试，符合抓取合规规则）。

判定口径：
- success：标题和正文都有，正文不是验证页；
- partial / failed：不算测试错误，但会打印原因，供人工判断是不是该平台改版了；
- 绝不允许：状态是 success 却命中拦截特征词（那就是把验证页当正文了）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dyz-plain-talk-decoder" / "scripts"))

import fetch_content as fc  # noqa: E402
from extractors.base import TOTAL_BUDGET, find_block_keyword  # noqa: E402

URL_FILE = Path(__file__).resolve().parent / "live_urls.txt"


def load_urls() -> list[tuple[str, str]]:
    if not URL_FILE.exists():
        return []
    pairs = []
    for line in URL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, url = line.partition(" ")
        pairs.append((kind.strip(), url.strip()))
    return pairs


@pytest.mark.live
@pytest.mark.parametrize("kind,url", load_urls() or [pytest.param("none", "", marks=pytest.mark.skip(reason="tests/live_urls.txt 为空或不存在"))])
def test_live_fetch(kind: str, url: str):
    t0 = time.monotonic()
    r = fc.fetch(url)
    elapsed = time.monotonic() - t0

    print(f"\n[{kind}] {url}\n  status={r['status']} platform={r['platform']} "
          f"reason={r['error_reason']!r} 标题={r['title'][:30]!r} 正文={len(r['content_text'])}字 用时={elapsed:.1f}s")

    # 硬性要求：预算内返回、合法结果
    assert elapsed <= TOTAL_BUDGET + 5, f"超出时间预算：{elapsed:.1f}s"
    assert r["status"] in ("success", "partial", "failed")
    if kind == "wechat":
        assert r["platform"] in ("wechat", "web")

    # 红线：success 不能带拦截特征（短正文才检查，长文里出现“验证码”等词是正常的）
    if r["status"] == "success":
        assert r["title"] and r["content_text"]
        if len(r["content_text"]) < 1500:
            assert not find_block_keyword(r["content_text"]), "把验证页当成了正文"
    else:
        assert r["user_hint"], "失败时必须给用户指引"
        assert r["content_text"] == "" or r["status"] == "partial"
