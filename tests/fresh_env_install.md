# P4-05 全新环境安装实测记录

日期：2026-11-20。环境：Windows 本机，系统临时目录下新建 `p405-b4ab/`，
内含 Skill 本体副本（与仓库 `dyz-plain-talk-decoder/` 逐文件 diff 一致，去掉 `__pycache__`）、
全新虚拟环境 `venv/`（Python 3.14.6）与零依赖裸环境 `venv-bare/`。
安装步骤完全按 README：先 `pip install -r requirements.txt`（核心），可选能力按需再装。
本记录只放结论与证据摘要；原始 JSON/日志留在临时目录，不随仓库分发。

## 一、按 README 安装与图文抓取

| 步骤 | 结果 |
| :--- | :--- |
| 复制 Skill 本体、建 venv、装核心依赖 | 成功 |
| `check_env.py`（装可选依赖前） | 正常输出 JSON；**发现真实缺陷**，见第四节 |
| `fetch_content.py` 抓 `https://www.ruanyifeng.com/blog/2026/11/weekly-issue-420.html` | `success`，标题「科技爱好者周刊（第 420 期）：最分心的一年」，正文 5410 字，`extraction_method=web_fetch` |
| 按 README 可选步骤 `pip install "yt-dlp>=2025.1"` | 成功（2026.10.26） |
| 重跑 `check_env.py` | yt-dlp `ok=true`、版本 2026.10.26；`video_subtitles=true`、`video_transcribe=true`（系统有 ffmpeg），与实际能力一致 |

## 二、视频路径实测（装 yt-dlp 后）

| 链接 | 结果 | 判定 |
| :--- | :--- | :--- |
| B站 `BV1AdkMBKEf9`（固态电池是个啥？160.7s） | `partial` / L0 / `asr_not_configured`，正文为空，hint 引导配 ASR 或粘贴，明说“不会只凭标题和简介下结论” | 正确：该视频无公开软字幕（与 K-08 记录一致），未配 ASR 时诚实停下 |
| `b23.tv/BV1nKPLzuEZ5`（1403s 无字幕），设 `ASR_PROVIDER=local_whisper` | `partial` / `missing_dependency`，hint「语音转文字缺少组件 faster-whisper。运行：pip install faster-whisper；或把字幕/文字稿贴给我。」退出码 0，无堆栈 | 正确：短链解析成功、L0 拿到元数据、缺组件诚实报错并给安装指引 |
| YouTube `jNQXAC9IVRw`（Me at the zoo，19s） | `success` / L1 / `extraction_method=subtitle`，265 字带 `[mm:ss]` 时间戳 | 正确：L1 字幕路径在全新环境跑通 |

B站 L1 成功样本仍缺（K-08 残留，非本次环境问题）。

## 三、零依赖裸环境（R-11 解除）

`venv-bare/` 不装任何第三方包：

- `check_env.py`：退出码 0，单份 JSON；`paste_text=true`、`fetch_articles=false`、
  `video_subtitles=false`、`video_transcribe=false`；hint 逐条给出
  `pip install requests / beautifulsoup4 / readability-lxml` 指引。
  本机 PATH 上有 yt-dlp 命令行程序但无 Python 库，正确报 `ok=false` 并说明
  “检测到 yt-dlp 命令行程序，但本技能的脚本需要 Python 库”——正是第四节缺陷的真实场景。
- `fetch_content.py` 抓真实链接：退出码 0，`failed` / `missing_dependency`，
  hint「缺少抓取所需的组件：requests（运行 pip install requests）。可以先把正文复制后粘贴给我，不影响使用。」
  正文为空，不脑补、无堆栈。

## 四、实测发现并修复的缺陷（check_env.py）

1. **命令行 yt-dlp 误报可用**：旧版在 `import yt_dlp` 失败时回退检测 PATH 上的
   `yt-dlp` 可执行文件并报 `ok=true`，但视频脚本用的是 Python 库，同一环境实际抓取
   报 `missing_dependency`——检测口径不一致，会让 Agent 以为能抓视频。
   修复：只认 Python 库；仅有命令行程序时 `ok=false`，hint 说明原因并给
   `pip install yt-dlp`。
2. **版本号取不到导致过旧提示失效**：`yt_dlp` 顶层没有 `__version__`（在
   `yt_dlp.version` 子模块），旧版恒报 `unknown`，120 天过旧升级提示永远不会触发。
   修复：`unknown` 时回退读 `yt_dlp.version`。

两条修复各加一条离线回归测试（`tests/test_extractors.py::TestCheckEnv`），
全套离线测试 **151 通过**（9 条 live 标记按默认排除）。修复已同步到全新环境副本复验（第一节）。

## 五、画像真实读写（R-03 解除，用户已同意）

在真实路径（用户主目录下的 `.plain-talk-decoder/profile.md`）按
`references/user-profile.md` 走了完整周期，内容全部虚构（初中教师/临川/降噪耳机/人体工学椅/预算 1500）：

1. **建立**：先确认路径不存在，再按手册模板创建文件夹与文件（状态：已建立、当天日期、各节齐全）；
2. **查看**：读回内容与写入一致，格式与手册模板一致；
3. **更新**：模拟“预算改 2000 以内、新增买备课平板”——只改预算字段、新增一条带当天日期的决定，
   其余字段逐字未动（程序断言验证）；
4. **删除**：删除文件与文件夹，断言两者均不存在。测试后真实路径恢复干净。

全程画像内容未进入任何脚本参数或搜索词（本周期未调用脚本）。

## 六、端到端：全新会话按已安装技能解码（通过）

会话 `581832f8-b683-513e-af22-36515e2fe70c`：只给临时技能副本路径与 venv 解释器路径，
用户请求「讲人话：周刊 420 期链接」＋「这次不用画像」。对照真实工具记录核验（不只信自报）：

**流程合规**：

- 先读 SKILL.md，再按步骤只读命中的 6 份手册（user-profile / environments / mode-digest /
  mode-factcheck / calibration / style-plain-talk），未读 M1/M2/M3/M5 与场景卡——工具记录
  恰好 7 次 Read，无开发仓库文件；
- 「这次不用画像」按 user-profile.md 规则 8 处理：未读未写画像文件、未追问建画像（无 Write 调用）；
- Bash 恰好 2 次：check_env 一次、fetch_content 一次，失败不重试规则未被触发（抓取 success）。
  **确实用的是全新 venv**：check_env 输出报 openai/faster-whisper 缺失，而仓库 venv 两者均已安装
  （openai 3.57.0 / faster_whisper 1.2.1），排除误用仓库环境；
- 分类 M4 并说明理由（个人策展周刊、无带货诉求）；标准版核查 3 条；3 次 WebSearch 的搜索词
  逐条核对，全部只含内容名词（GTA6 销量 / 黑猩猩病毒研究 / 西班牙梁龙化石），零画像、零仓库信息；
- 三态标注正确（2 条 `[已核实]`、1 条 `[存疑/有出入]`），引用链接来自真实搜索结果；
  再次查出周刊把“黑猩猩病毒”研究夸大（700 万 vs 约 400 万年前、“无法进化出智能”、免疫是假说），
  与 P4-03 B 档演练发现一致，校准规则生效；
- 报告按模板：结论先行、来源交代、论证骨架、可信度、给你的建议；无视频任务故未跑 cleanup，符合第 10 步。

**如实记录的观察（不构成发布阻塞）**：

1. 能力探测被合并进 check_env 与首次抓取，未按 environments.md 单独探测——结果等价，属执行裁量；
2. `content_text` 是纯文本、丢失原文超链接 URL，会话无法确证周刊引用的研究就是查到的那篇
  （主题高度吻合但靠推断），会话已如实声明。属提取器已知特性，改进需先改 PRD，本次不动；
3. 该站 `author`/`publish_date` 返回空，报告用标题与 URL 交代来源并声明未独立核实；
4. 篇幅约 800 字超标准版目标，会话引用 style-plain-talk.md“篇幅是参考，不是硬限制”
  （第 100 行，属实）并按保留顺序优先保可信度与建议——合规；
5. 梁龙条目标 `[已核实]` 同时注明“阶段性临时陆桥”的简化说法，会话自认取态偏宽——裁量范围内，已如实记录。

## 七、未验证与局限

- **macOS / Linux（R-10）**：本机只有 Windows，无法实测；代码按跨平台写法
  （pathlib、UTF-8 自设置、参数列表调用外部程序），如实保留“未在这两个系统上跑过”。
- B站 L1 软字幕成功样本仍缺（K-08）。
- T2 平台（微博/X/小红书/抖音）真实成功抓取未覆盖（R-09，选做未做；T2 失败不算缺陷）。
- 云端 ASR 未在全新环境重测（Phase 3 已用仓库环境实测通过，脚本与依赖清单未变）。

## 八、2026-11-25 候选分支 Windows 本地复验（`v0.1.0` 就绪核验）

日期：2026-11-25。对象：本地候选分支 `autopilot/v010-release-readiness`（M4 就绪核验快照）。在 Windows 本机独立执行以下 4 项硬证据核验：

1. **分发包归档自包含检验**：运行 `git archive --format=zip HEAD dyz-plain-talk-decoder` 检查归档内容——共包含 **30 个分发文件**（`SKILL.md`、14 份 `references/` 手册、13 个 `scripts/` 运行文件与 2 份 `requirements*.txt`），**0 个缓存、测试、开发配置或隐私文件**混入。
2. **隔离虚拟环境按 README 安装与自检**：将归档解包至系统临时目录，新建隔离 `venv`（Python 3.14.6），按 `README.md` 运行 `pip install -r requirements.txt`，再执行 `python scripts/check_env.py`——退出码 0，标准输出为单份合法 UTF-8 JSON，`paste_text=true`、`fetch_articles=true`，未安装的可选依赖（视频/ASR）诚实降级并给出普通人可读安装指引，无异常堆栈。
3. **本机 `uv 0.13.9` 不联网 3 条脚本冒烟**：在离线条件下直接执行 `uv run scripts/check_env.py`、`uv run scripts/lint_report.py --tier fast --offline` 与 `uv run scripts/render_card.py`——3 条命令均以退出码 0 结束，标准输出 JSON 与生成的单页 HTML 卡片结构合规。
4. **真实 Chromium 浏览器交互验收（`ego-browser`）**：在本地真实 Chromium 浏览器中打开由现行 `render_card.py` 渲染的卡片并实测交互：
   - `examples/01-self-audit-readme.html`：历史声明正常展示，0 外链，打印样式（`@media print`）下工具条自动隐藏，点击「只看存疑与避坑」精准隐藏非 caution 条目，点击「看全文」完整恢复，**0 条控制台报错**；
   - `examples/03-prepaid-and-finance.md` 渲染卡片：自然段首段 `lead-item` 筛选保护生效、识别 2 处 caution 条目，**0 条控制台报错**；
   - `examples/05-clean-article-no-nitpick.md` 渲染卡片：干净样本 `caution_items=0` 零误标，**0 条控制台报错**。

**本节复验范围边界**：本复验仅覆盖 Windows 本机离线打包、基础层隔离安装、`uv 0.13.9` 离线冒烟与浏览器本地分享卡交互，**不含**实网抓取、macOS/Linux 实机安装、多宿主自然触发、`.claude-plugin/` 插件市场实装或真实 ASR 端到端转写。
