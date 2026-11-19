# CLAUDE.md

本仓库的施工规范统一写在 AGENTS.md，Claude Code 同样遵守：

@AGENTS.md

## Claude Code 补充说明

- **开工顺序**：每次新会话先读 `plain_talk_decoder_skill_prd.md` 中与当前任务相关的章节，再读 `docs/construction-plan.md` 确认当前进度和下一个任务。
- **未获许可不施工**：用户只让写或改文档时，不创建 `dyz-plain-talk-decoder/` 下的任何文件。
- **Phase 关口**：一个 Phase 的验收标准全部自查完后，汇报并停下，等用户明确说继续。
- **运行 Python**：使用仓库根的 `.venv`。在 Git Bash 下解释器路径是 `.venv/Scripts/python.exe`。
- **本机测试 Skill**：需要实际试用 Skill 时，先征得用户同意，再把 `dyz-plain-talk-decoder/` 安装到用户级 Skill 目录；不要覆盖用户已有的同名 Skill。
- **联网与下载**：冒烟测试会访问真实网站、下载音视频，运行前告诉用户要访问哪些地址。
- **子代理**：评测解码质量时可以用子代理做“不带上下文的读者”来盲评输出，但结论要自己核对后再汇报。
