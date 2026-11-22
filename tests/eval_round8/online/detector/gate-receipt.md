# 原版门禁回执：Python 3.15 单题 / detector quick

**实际结果：compose 0；fix 与 source 联检 0；单独只读 source 复验 0。** 没有拒绝后重试，没有改写 gate。它检查内部一致性，不评价观点真假或 BS 分数正确性。

## 执行条件与命令

工作目录为项目根目录，PowerShell 5，本机 Python 3.14.6。固定提交、唯一执行的上游脚本及已审查内容见 `protocol.md`。脚本在执行前再次以 `Get-FileHash -Algorithm SHA256` 对比 `0C692BADA38F774D3BA75D43951C90375B76F485CB1C0F52990AA1AEDE12E754`；命令还要求正式报告此前不存在。以下变量及三个 Python 调用与实跑一致；退出码是每条命令后即时读取的 `$LASTEXITCODE`，不是对输出的推断。

```powershell
$t='.tmp/detector-online-b42a/skills/analysis/bullshit-detector/scripts/tally.py'
$a='tests/eval_round8/online/detector'
$base="$a/bs-report-python-utf8-2026-11-20"
$r="$base.md"; $sh="$base.shell.md"; $src="$a/source.md"
$env:BULLSHIT_DETECTOR_SOURCE=(Resolve-Path $src).Path
$env:BULLSHIT_DETECTOR_REPORTS=(Resolve-Path $a).Path
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
python -I -S -B -X utf8 $t $r --compose $sh      # compose_exit=0
python -I -S -B -X utf8 $t $r --fix --source $src # fix_source_exit=0
python -I -S -B -X utf8 $t $r --source $src       # readonly_source_exit=0
```

复核者若只想复验，可从 `protocol.md` 指明的提交还原并审查同一份 `tally.py`，核对摘要，然后运行最后一条只读命令，不必覆盖原报告。

## 输出与时间

```text
composed 3 claims → tests\eval_round8\online\detector\bs-report-python-utf8-2026-11-20.md
compose_exit=0
claim rows: 3   searched (M): 3
  confirmed      1
  false          2

**Tally: 3 claims extracted, 3 individually source-checked** — 1 confirmed, 2 false.
✔ tally reconciles and header checks pass
fix_source_exit=0
✔ tally reconciles and header checks pass
readonly_source_exit=0
```

以上是关键输出节选，不是完整控制台逐行转录。`--fix` 同时补写了同名运行记录的 `claims.extracted=3`、`claims.checked=3`、`claims.dropped_ambiguous=0`、`wall_seconds=200`。只读复验与 `--fix` 均有相同的 **3 条非阻断警告**：第 2、3 项未检测到五个空白分开的原话词，另有 `effort` 未声明（环境未提供，未猜测）。这些警告未被屏蔽。

- 任务开始读钟：`2026-11-20T23:53:37.9496239+08:00`。
- 核查内容和源页归档均完成、准备写运行记录时读钟：`2026-11-20T23:56:58.8165101+08:00`。官方记录从开始到此按整数秒生成 3 分 20 秒。实际页脚：`run: 3m20s, searches 5, tools 48, coverage 0, per claim 67s, mode quick, model gpt-6.2-astra`。
- 门禁开始：`2026-11-20T23:57:24.1345299+08:00`；三条命令和下述中文补核完成：`2026-11-20T23:57:24.7523790+08:00`。从任务开始到门禁完成约 **226.80 秒**；它比运行记录中的内容核查时间长，二者不是同一截止点。

## 中文原话专门补核

用宿主 PowerShell 的 `ConvertFrom-Json` 逐行读取正式 claims，要求 `quote` 非空，调用 `$text.IndexOf($q,[StringComparison]::Ordinal)` 比较原始 Unicode 字符，任一未命中即报错。不跑额外下载的程序，不改门禁。实际结果如下，偏移从 0 起算：

```text
ordinal_quote_1 offset=0 chars=30
ordinal_quote_2 offset=33 chars=28
ordinal_quote_3 offset=62 chars=13
```

## 最终文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| 正式 `bs-report-python-utf8-2026-11-20.md` | `E35C5490D227F96FA772C2D7EFA2C5166755467EC495A735238C56479BC6D506` |
| 同名 `.run.json` | `2915B8F76F0E6576B3F8B718C852582C7636CDD4DCB4302CFAC574C40A6E8E55` |
| 同名 `.claims.jsonl` | `AFB7B5CBEED5B41B35E743E7EC9B391C56DEFDA3091A774B7A6888661F5ECCF1` |
| `source.md` | `04E9316C16AE92EEDF03DF123FB96E46B94F034312A4AAB4B1F2C3C2F6E78842` |
| 同名 `.shell.md` | `7CBDC3EABF0C5FCF8B9A2020F78A842560C6CD9723C1FCC70FC4BC25C219FD0F` |
| `primary-pages.zip` | `6FE2D3A34C3ED6D01508385AE4A97895862319A7CCC219F01BF30DBAC0DF75E8` |

清理回执：`own_temporary_tree_exists=False`，`tmp_root_exists=False`。删除的是本臂专用暂存树和本次创建且不再存有其他文件的根 `.tmp`；正式报告和 sidecar 均留在本目录。
