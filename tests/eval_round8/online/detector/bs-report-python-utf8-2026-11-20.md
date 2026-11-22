# 核查报告：Python 3.15 会替你转换旧文件吗？

**Source:** no permalink — 用户粘贴的假设开发者消息，见[完整原文](source.md)；没有作者、平台或发布日期资料，不是媒体引语。  
**Checked:** 2026-11-20 · bullshit-detector 0.14.0  
**Mode: quick** — 全文都是与改不改代码有关的核心断言，没有跳过的附带事实。逐项查证；未测报道覆盖面，也省略了单列的反方质询。  
**BS score: 7/10 — 默认设置说对了，但两个直接影响迁移决定的推论都错了。**

## 这条消息说了什么

消息把 Python 3.15 的默认编码变化与已有文本联系起来。它还表示程序不再有关闭模式的选择。

## 支撑结论的断言

| # | Claim | Type | Verdict | Evidence |
|---|---|---|---|---|
| 1 | Python 从 3.15 起默认启用 UTF-8 模式。[第 1 句] — "从 Python 3.15 开始，UTF-8 模式默认开启。" | factual | ✅ confirmed | 已定稿的 PEP 686 将这一变化明确规定在 Python 3.15。文本文件未指定编码时通常按 UTF-8 读写；指定了编码的操作仍按指定值，不是强制每个文件都使用 UTF-8。 ([source](https://peps.python.org/pep-0686/#enable-utf-8-mode-by-default)) |
| 2 | [升级到 Python 3.15] 会替用户把已有的本地编码文本统统转成 UTF-8。[第 2 句前半] — "以前用本地编码保存的文本现在都会被自动转换成 UTF-8" | factual | ❌ false | PEP 686 的兼容性说明警告，同一程序可能出现 UnicodeError、乱码乃至静默的数据损坏；若本地编码不是 UTF-8，需要指明正确编码。升级不会改动磁盘上的旧字节，读取也不等于写回。最善意的理解是后续以新默认值写出的文本可用 UTF-8 保存，但那要求程序真的写文件，不能推出旧文件会自动转换。 ([source](https://peps.python.org/pep-0686/#backward-compatibility)) |
| 3 | [Python 3.15] 不再提供关闭 UTF-8 模式的办法。[第 2 句后半] — "程序也不能再关掉这个模式。" | factual | ❌ false | 解释器文档明确给出两种启动时退出默认模式的方法：`python -X utf8=0 app.py`，或者在启动前设置环境变量 `PYTHONUTF8=0`。若只指已经在跑的解释器，确实不能中途切换；但这是设置时点的限制，不是 3.15 取消开关。即使模式开启，程序也可以给单个文件指定编码。 ([source](https://docs.python.org/3.15/library/os.html#python-utf-8-mode)) |

## 附带断言

无。



**Tally: 3 claims extracted, 3 individually source-checked** — 1 confirmed, 2 false.

**Ambiguous: 0 claims dropped before verification** — 没有因指代不清而丢弃的断言。

## 夸张信号

“都会被自动转换”把新的读写默认值说成旧文件的自动迁移；“不能再关掉”把不能在当前进程中途切换说成所有时候都不能关闭。原文的“所以”没有解释这两步推论为什么成立。

## 动机分析

原文没有产品、付费链接或行动号召，无法从这段假设消息判断作者动机。错误本身不构成故意误导的证据。

## 要不要改代码？

**不必因为升级就批量改代码或转换文件。** Python 换的是没写编码时采用的读法，不会替你重存旧文件；以前怎么存，磁盘上的字节就还是什么样。如果代码读的是已知的 GBK 旧档却只写 `open(path)`，应改为 `open(path, encoding='gbk')`；如果已经明确指定正确编码，则不用为这项变化修改。如果新文件约定为 UTF-8，就明确写 `encoding='utf-8'`，用真实旧档做一次回归；需要过渡时，可在下次启动解释器时用 `-X utf8=0`，而不是在程序跑到一半时设置。参见 [Python 3.15 官方变更说明](https://docs.python.org/3.15/whatsnew/3.15.html#whatsnew315-utf8-default) 和 [PEP 686 的兼容性建议](https://peps.python.org/pep-0686/#backward-compatibility)。

*run: 3m20s, searches 5, tools 48, coverage 0, per claim 67s, mode quick, model gpt-6.2-astra*
