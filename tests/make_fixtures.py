"""生成较长的样本页（避免手写大段重复内容）。只在开发时运行一次，不随 Skill 分发。

用法：.venv/Scripts/python.exe tests/make_fixtures.py
"""

from pathlib import Path

FIX = Path(__file__).parent / "fixtures"

PARA = (
    "第{i}段：图形验证码通过让用户识别扭曲字符来区分人和程序，这类机制的强弱取决于识别难度与"
    "风控策略的配合，单独依赖任何一种手段都不够稳妥。"
)


def main() -> None:
    head = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<title>验证码是怎么工作的：一篇科普</title>"
        '<meta property="og:title" content="验证码是怎么工作的：一篇科普"></head>'
        "<body><article><h1>验证码是怎么工作的：一篇科普</h1>\n"
    )
    body = "".join(f"<p>{PARA.format(i=i)}</p>\n" for i in range(1, 25))
    (FIX / "web_long_article_with_captcha_word.html").write_text(
        head + body + "</article></body></html>\n", encoding="utf-8"
    )
    print("已生成 web_long_article_with_captcha_word.html")


if __name__ == "__main__":
    main()
