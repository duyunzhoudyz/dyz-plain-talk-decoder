**只有第一句对：Python 3.15 默认开启 UTF-8 模式，但不会自动转换旧文件，也没有取消关闭开关。**

变的是 Python **没被告知编码时，怎样读写文本**，不是硬盘上的文件。比如一份用 GBK 保存的中文名单：升级 Python 不会改动它。用 `open("名单.txt")` 读取时，3.15 默认按 UTF-8 解释原来的字节；可能报 `UnicodeDecodeError`，也可能读出乱码。要把名单真正转成 UTF-8，需要先按 GBK 正确读出文字，再按 UTF-8 写入新文件。

**要不要改代码，看程序有没有依赖旧默认值。** 如果已经写明了正确的 `encoding`，不用为这次变化改。如果以前省略编码，现在还要读取 GBK 名单，就写 `open("名单.txt", encoding="gbk")`；如果接收方仍要求 GBK，写出文件时也要指定 GBK。不必把所有文件改成 UTF-8。

暂时改不完，可以用 `python -X utf8=0 app.py` 启动程序，或在启动前设置环境变量 `PYTHONUTF8=0`。这会关闭该次运行的 UTF-8 模式，不会修改任何已有文件。

原始出处：[Python 3.15 更新说明](https://docs.python.org/3.15/whatsnew/3.15.html)说明了省略编码时的变化；[PEP 686 规范](https://peps.python.org/pep-0686/#enable-utf-8-mode-by-default)列出了两种关闭方式；[`open()` 官方文档](https://docs.python.org/3.15/library/functions.html#open)解释了文本模式如何解读文件字节。
