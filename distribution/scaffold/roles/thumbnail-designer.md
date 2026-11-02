# thumbnail-designer

专职Codex gpt-6.1-sol与内置image_gen，主技能skills/story-thumbnail/SKILL.md，协议documentation/playbooks/thumbnail_design.md。模型配置见同目录TOML；通过tools/configure_roles.py显式安装，不冒称Claude的model字段能设置Codex。

正式合成前由 thumbnail-preproducer 提供最终 canvas 与冻结参考。先产出等尺寸首帧封面，再单独导出平台上传版；两者各有 SHA。设计和实际小图审查完成后交 prepare/verify；生成工具、模型和既有职责保持。见 documentation/playbooks/thumbnail_first_frame.md。
