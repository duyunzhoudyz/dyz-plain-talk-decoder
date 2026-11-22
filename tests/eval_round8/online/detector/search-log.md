# detector 独立检索流水（按批次顺序）

本文件随检索写入；这是联网单题，不属于冻结的离线四题。输入来源为用户粘贴的假设消息，未提供作者、发布日期、平台、浏览量或原始文章 URL，未将其当作媒体报道。无画像输入。

| 批次 | 核查对象 | 发出的完整查询 |
|---|---|---|
| 1 | 第一句：默认开启的版本与范围 | Python 3.15 default UTF-8 Mode What's New PEP 686 version change Python documentation |
| 1 | 第二句前半：默认编码是否自动改写旧文件 | Python 3.15 UTF-8 mode existing files legacy encoding automatically convert bytes decode open encoding Python IO reading text files PEP 686 |
| 1 | 第二句后半：还能否关闭该模式 | Python 3.15 disable UTF-8 Mode startup -X utf8=0 PYTHONUTF8=0 Python 3.15 command line documentation |
| 2 | 第 2 项反向核查：首轮主要是二手教程；原提案是否承认新默认值会错误解码、而非自动修复旧数据？ | site:peps.python.org/pep-0686 backward compatibility legacy encoding silently corruption file mode reading writing exchanging data UnicodeDecodeError |
| 2 | 第 3 项善意解释：首轮未区分运行中和下一次启动；找解释器关于时点的明文。 | site:docs.python.org/3.15/library/os.html "UTF-8 Mode" "startup" only be enabled |
