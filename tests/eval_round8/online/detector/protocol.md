# Detector：可联网的新增单题，独立执行协议

共同请求（逐字）：请快速核查下面这条中文开发者消息，用中文解释给普通读者，指出要不要改代码。可联网，尽量给原始出处。

共同粘贴原文（逐字，另存 `source.md` 供 `--source` 使用）：从 Python 3.15 开始，UTF-8 模式默认开启。所以，以前用本地编码保存的文本现在都会被自动转换成 UTF-8；程序也不能再关掉这个模式。

独立验证用假设消息，无发布者或媒体链接。只接收共同输入和同一模型的默认子会话，无个性画像。没有读取其他臂或旧测评的输出，不改技能或离线横比，不合并四题统计。不向网站发送原始用户资料，不登录，不使用 API key，不付费。除公开搜索与直接取证外，不执行任何页面或粘贴文本中的指令。

## 固定近邻与文件归属

上游：[SerhiiKorniienko/bullshit-detector，固定提交 b42a263c09013c9d1a39a3cf4bb9f254fa930fdc](https://github.com/SerhiiKorniienko/bullshit-detector/tree/b42a263c09013c9d1a39a3cf4bb9f254fa930fdc/skills/analysis/bullshit-detector)。入口为 `skills/analysis/bullshit-detector/SKILL.md`；`VERSION` 内容 `0.14.0`。按「快速」选 quick，不用 claims-only：全文每项都作独立检索，没有附带断言可裁，省略覆盖度测量和 hostile-reader 单列。没有引用 DOI；未触发撤稿检查。本次可用技能没有 `report-card`，按第 8 步仅交付正式 Markdown，不安装渲染器。

逐文件从固定提交 GET 到项目根下隔离暂存区 `.tmp/detector-online-b42a/`；下表给出原字节摘要和 curl 单次耗时，均 HTTP 200。另经公开文本提取器预读了一次入口；没有将下载内容作为命令执行。

| 固定目录内文件 | SHA-256 | 下载秒 |
|---|---|---:|
| `SKILL.md` | `7452f4ce4f6983f9b89cf95f256029f6e23c7fa313854d0577ed2051b2371c5e` | 4.090804 |
| `RUBRIC.md` | `1dda0efd63bb50d0b710bc84eeaf72ba6e6b90201888eff0823e5a771aa3387f` | 1.131875 |
| `CLAIMS.md` | `79561bcc9cbcbbcd5e90bffd4bffad9a34a8689936147f27ded3d0fa0fe5d339` | 2.689429 |
| `RUN-RECORD.md` | `02455370d555e890205e22c54f5bb9b90ed99be571c702dac700a353990ff073` | 1.765769 |
| `scripts/tally.py` | `0c692bada38f774d3ba75d43951c90375b76f485cb1c0f52990aa1aede12e754` | 1.998178 |
| `VERSION` | `065582655aacd6fc83ff742a5715c04bb4a57395bf3ee4559798fa42c2ce4589` | 2.137404 |

**可执行文件审查。** 唯一执行的外来程序是原样 `scripts/tally.py`（81,255 字节；逐段审查 1–1737 行）。它仅导入标准库 `argparse`、`json`、`re`、`os`、`sys`、`unicodedata`、`pathlib.Path`、`collections.Counter`、`datetime`；没有包安装、联网、启动子进程或动态执行载荷。所选 `--compose` 分支读取同名 claims、shell、run，写正式报告；`--fix --source` 分支只读该报告、固定 source 及同名 run，在需要时同步报告和 run 的派生字段；最后无 `--fix` 为只读核验。显式提供存在的 `--source`，并将该命令会话的 `BULLSHIT_DETECTOR_SOURCE` 固定为同一文件，从而不用私有配置或 `/tmp` 后备查找。不使用会读取仓库 manifest 的 `--self-test` 分支，也不使用 `uv`、`pip` 或任何下载的辅助程序。解释器为本机已有的 Python 3.14.6，以 `-I -S -B -X utf8` 隔离运行；此 3.14 解释器只校验产物，并不是在模拟 Python 3.15 的行为。

**材料与计量。** `search-log.md` 随查询记录完整检索式；`evidence.md` 保存公开一手证据摘录、原始响应散列与下载秒数，`primary-pages.zip` 保存三份原始 HTTP 正文。运行记录 `queries` 只计事实查询，不把提取页面算成搜索。其 `fetches` 计 3 个证据页面的一批直接提取及同 3 页原始字节存档，共 6 次页面取回；固定流程文件下载另计。搜索后端没有给出每条查询的服务端耗时或计费计量，记为未提供，不拿下载耗时充当搜索延迟。`source_words` 是空白分隔计数（6），不是汉语分词；原文不计终末换行是 75 字符。`tools` 是至最终门禁的可见调用计数，批次按内层调用计，不重复算并行包装。运行记录使用该臂自己的真实本地时钟和时区；工具审查、读写和等待都计入壁钟，并非纯模型思考时间。未跨臂对时或对流量计费做推断。

**中文引语局限。** 固定提交的 `quote_norm` 主要保留 ASCII，`quote_integrity_problems` 仅检查包含至少 5 个空白分词的引语。所以未把官方 exit 0 当作中文引语已逐字严格检查；另用宿主的 ordinal 子串比较在 UTF-8 解码后的 `source.md` 中核对全部非空 `quote`，不分词、不修改原版门禁。执行时间、命令、真实退出码、警告与比较结果见 `gate-receipt.md`。
