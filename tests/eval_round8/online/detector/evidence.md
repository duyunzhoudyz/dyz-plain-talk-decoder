# 本臂取得的 Python 一手材料

被核查对象仅是 [source.md](source.md) 里的粘贴文本。以下才是核查依据；没有把搜索服务的自动摘要或技术博客当作原始出处。只引与本题有关的原文，HTML 字节数和 SHA-256 来自本次公开页面下载；网页会更新，这些散列不是对未来网页的要求。

| 发布者与定位 | 本次直接 GET | 下载用时 | 原始响应字节数 | SHA-256 |
|---|---|---:|---:|---|
| Python PEP 编辑体系，[PEP 686](https://peps.python.org/pep-0686/) | HTTP 200 | 4.588466 秒 | 22186 | `fd72458a03c22033bf4120a5a32867c08648b8f5f0577f71645138bee43cedb8` |
| Python 文档，[What’s New in Python 3.15](https://docs.python.org/3.15/whatsnew/3.15.html) | HTTP 200 | 3.938206 秒 | 446255 | `2bb34626e695e8b51a8d077f8981ba0303c0a7c163a2e1b5c838c26c6f2dd748` |
| Python 文档，[os / Python UTF-8 Mode](https://docs.python.org/3.15/library/os.html#python-utf-8-mode) | HTTP 200 | 5.307154 秒 | 791660 | `f748bd918959648cad6bc12e4c1eeda9c1b3d622fcB6ae8045e02ff2bc942b11` |

**默认开启。** PEP 686（Final，Python-Version: 3.15），「Enable UTF-8 mode by default」：“Python will enable UTF-8 mode by default from Python 3.15.” 变更说明将影响限定为：“This only applies when no `encoding` argument is given.” 这是同一个 Python 项目的规范与实现文档，不是两家独立报道。

**不是自动转码。** PEP 686，「Backward Compatibility」：“When a Python program depends on the default encoding, this change may cause `UnicodeError`, mojibake, or even silent data corruption.” 新的默认值决定如何解释或写出字节，不会把旧文件自动重写。来源没有承诺遇到旧编码必定失败，因而报告说可能出现错误，而不是每个旧文件都打不开。

**仍可关闭，但要区分何时生效。** PEP 686：“Users can still disable UTF-8 mode by setting `PYTHONUTF8=0` or `-X utf8=0`.” `os` 文档限定：“The Python UTF-8 Mode can only be disabled at Python startup.” 不能在运行中切换，不等于下一次启动没有选择。

各问均可由一份一级文件裁断，关键行只标一个原始出处。未把转载或同一提案的复述累计为独立印证。未对本机 Python 旧版本做模拟，也不宣称已在 3.15 上运行过任何用户代码。
