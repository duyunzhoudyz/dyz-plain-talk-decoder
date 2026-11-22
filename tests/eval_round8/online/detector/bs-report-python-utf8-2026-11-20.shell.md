# 核查报告：Python 3.15 会替你转换旧文件吗？

**Source:** no permalink — 用户粘贴的假设开发者消息，见[完整原文](source.md)；没有作者、平台或发布日期资料，不是媒体引语。  
**Checked:** 2026-11-20 · bullshit-detector 0.14.0  
**Mode: quick** — 全文都是与改不改代码有关的核心断言，没有跳过的附带事实。逐项查证；未测报道覆盖面，也省略了单列的反方质询。  
**BS score: 7/10 — 默认设置说对了，但两个直接影响迁移决定的推论都错了。**

## 这条消息说了什么

消息把 Python 3.15 的默认编码变化与已有文本联系起来。它还表示程序不再有关闭模式的选择。

## 支撑结论的断言

<!-- CLAIMS: load_bearing -->

## 附带断言

无。

<!-- CLAIMS: incidental -->

<!-- TALLY -->

**Ambiguous: 0 claims dropped before verification** — 没有因指代不清而丢弃的断言。

## 夸张信号

“都会被自动转换”把新的读写默认值说成旧文件的自动迁移；“不能再关掉”把不能在当前进程中途切换说成所有时候都不能关闭。原文的“所以”没有解释这两步推论为什么成立。

## 动机分析

原文没有产品、付费链接或行动号召，无法从这段假设消息判断作者动机。错误本身不构成故意误导的证据。

## 要不要改代码？

**不必因为升级就批量改代码或转换文件。** Python 换的是没写编码时采用的读法，不会替你重存旧文件；以前怎么存，磁盘上的字节就还是什么样。如果代码读的是已知的 GBK 旧档却只写 `open(path)`，应改为 `open(path, encoding='gbk')`；如果已经明确指定正确编码，则不用为这项变化修改。如果新文件约定为 UTF-8，就明确写 `encoding='utf-8'`，用真实旧档做一次回归；需要过渡时，可在下次启动解释器时用 `-X utf8=0`，而不是在程序跑到一半时设置。参见 [Python 3.15 官方变更说明](https://docs.python.org/3.15/whatsnew/3.15.html#whatsnew315-utf8-default) 和 [PEP 686 的兼容性建议](https://peps.python.org/pep-0686/#backward-compatibility)。

<!-- RUN -->
