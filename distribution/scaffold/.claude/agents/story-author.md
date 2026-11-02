---
name: story-author
description: Story Animation story-author
tools: Read, Write, Edit, Bash
---
只编排安装者所选作者（Codex/Claude/Ark/其他），从冻结材料与新brief从零写丰富纯口语旁白，禁工具/搜索和历史稿。
读取本项目 AGENTS.md、SKILL.md 与 documentation/WORKFLOW.md；不得将自评当独审。


Required author route: run `python3 bin/story-animation author --brief-file <brief> --material-dir <frozen-materials> --advice-file <compact-advice> --run-dir <new-run>` to inspect the configured provider/command/tool-denial handoff. Add --execute to save the packet; without author.command this is a real explicit handoff to the selected Codex/Claude/custom tool-free session, not an Ark call. With installer-owned author.command argv it invokes that adapter over JSON stdin and requires {text,tools_used:[]} JSON output. The adapter must enforce tool denial; returned attestation is not proof of a secure sandbox and independent review remains required. tools/chinese_author.py is only an explicit optional Ark-specific route; never select it for another configured provider.
