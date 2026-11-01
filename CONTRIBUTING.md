# 贡献 / Contributing

欢迎提交可复现的问题、供应商适配器和参数改进。开始前阅读 distribution/scaffold/AGENTS.md 与 documentation/WORKFLOW.md。

1. 用合成数据复现，标明软件版本、操作系统和命令，不附密钥或私人作品。
2. 在独立分支做聚焦修改，不把真实制作状态、音效库、字体或媒体塞进仓库。
3. 运行 `python3 -m unittest discover -s tests -q`（工作目录 distribution/scaffold），并给出受影响的离线检查。
4. 供应商适配需说明能力、费用确认、未知请求处理和真实验证范围。单纯兼容接口不等于套餐可用或质量已验收。
5. 分发包的 manifest 记录文件哈希；源码改动后的发布需要统一更新版本、manifest 与安装验收，不能只修改代码却继续冒称旧广场包。

Submitted contributions must be yours to license under the project's MIT license. Preserve source notices and third-party rights. Tests use synthetic fixtures; provider access and human review are separate acceptance steps.
