# Story Animation
一个可独立运行的中文动画故事 workflow：研究与核心思想先行，国产模型写纯旁白，角色一致性、26种可编辑画风、原稿强制对齐字幕、缓存声音库、参数化章节转场和完整 scene clip 合成。

快速开始：
```sh
python3 bin/story-animation doctor
python3 bin/story-animation init
python3 bin/story-animation ceo plan --production-date 2030-01-07
python3 bin/story-animation schedule --production-date 2030-01-07
python3 bin/story-animation research check --material-dir examples/materials
python3 bin/story-animation episode create --production-date 2030-01-07 --title '自己的新故事' --review-only
```
配置供应商前这些命令不调用付费模型。完整使用读 AGENTS.md、SKILL.md、documentation/WORKFLOW.md 和 documentation/PROVIDERS.md。依赖安装与密钥/声音/字体由使用者自行配置，doctor 不会把离线可跑冒充生产已就绪。

`python3 -m unittest discover -s tests -q` 验证合成夹具，不写真实队列。每集自身 SQLite/table 与 vector 索引独立；上传默认演练，review_only永禁公开。

1.1新增：最终FA字幕的简/繁/英/日/韩可切换轨；保留review_only原集的独立pilot发布（private→封面→字幕→列表→显式privacy）；Codex gpt-6.1-sol专职thumbnail-designer。安装后阅读documentation/playbooks/，并显式运行python3 tools/configure_roles.py --execute配置本工作区角色。需要自己的force-ssl字幕OAuth授权；不继承制作方账号/授权/媒体。
