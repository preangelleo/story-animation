---
name: story-animation
description: 从冻结研究素材与核心思想，制作带角色一致性、原稿对齐字幕、克制声音和章节转场的中文动画故事。
---
读取 AGENTS.md 及 documentation/WORKFLOW.md，所有参数以 CLI --help 和 JSON 配置为准。先 doctor，缺能力只阻塞对应步骤，不伪造完成。
1. researcher 检索素材并归档事实/流传说法/演绎/版权；theme-planner 提炼 1—3 思想和 takeaway，生成材料包。research check 冻结SHA。
2. configured-author 按 author.provider 选择本地harness或provider；tools/chinese_author.py仅是可选Ark适配，从 从冻结包和新brief写纯旁白，禁所有工具、不读历史文案。丰富内容优先，不机械压短；--max-han-chars 0 无上限。
3. editor 独立审大纲、结构、全文、字幕语义单元；真实引用不得把编者总结冒充人物原话。
4. character-designer 先定妆，再逐镜实际带参考图生成。使用 harness 内置 image_gen；没有时明确让用户配置工具，禁止默默换付费 API。静图无运镜，画面延伸四边，不设字幕底带；重复必须显式引用原镜头。
5. 媒体保持冻结纯旁白 SHA，先 ElevenLabs 配音，独立 ASR 全文差异审查，再用原稿 Forced Alignment。字幕由 vendored smart_caption 语义分段，无标点、无单字/短闪；音轨与脚本版本变化使下游失效。
6. 最多三处约十秒视频按关键剧情而非等距选择。声音从小写库精确查找，命中复用，缺失才经授权生成；干声保持，SFX/BGM 只在关键处低音量使用。
7. chapter-designer 用 chapters render 参数化 HyperFrames 短片；C1 为本频道已选双字体设计，通用用户自带字体。章节必须插在完整 scene clip 边界，按 typed EDL 顺序拼接。全解码帧序列验证不能降低为抽帧。
8. render → burn → clock-qa → 独立终审 → qa/validate → 人工观看。机器不能听不构成全片停工理由；明确 pending_owner_review。
9. 可选 ship 前核独立频道 ID/profile，D+2 东京20时；默认演练，必须明确 --execute 和真实回读。review_only 永久禁上传。最后 table 为事实源，vector 只召回候选，72h/7d/28d复盘。

## 1.1 可切换字幕、独立pilot与封面
读取 skills/story-captions-multilingual/SKILL.md、skills/story-pilot-release/SKILL.md、skills/story-thumbnail/SKILL.md；不要把源review_only改为正式集。已接受媒体若另获当前使用者明确发布授权，以独立pilot记录按private视频→自定义封面→五字幕→列表→全部serving后显式privacy执行。专职封面由Codex gpt-6.1-sol+内置image_gen生成，参数来自本集冻结画风/角色，不靠全局私有脚本；实际320/160px视觉验收。使用tools/configure_roles.py --execute显式配置本工作区Codex角色。

1.2.0：正式合成前必须完成 thumbnail-preproducer → Codex thumbnail-designer → prepare/verify/consume；读取 documentation/playbooks/thumbnail_first_frame.md 和 documentation/PARAMETERS.md。第 0 帧禁止字幕，第 1 帧恢复原 cue，不移动整条字幕；新 final 必须有新版首帧 receipt。


Optional SQL/vector/comment intake/reply/topic ideas/dedup: all off by default. See documentation/LEARNING.md, schemas/learning.schema.json and `modules configure --help`. Author provider is configurable; research stays in the orchestrator and writers receive frozen materials plus compact independent angle advice.


Required author route: run `python3 bin/story-animation author --brief-file <brief> --material-dir <frozen-materials> --advice-file <compact-advice> --run-dir <new-run>` to inspect the configured provider/command/tool-denial handoff. Add --execute to save the packet; without author.command this is a real explicit handoff to the selected Codex/Claude/custom tool-free session, not an Ark call. With installer-owned author.command argv it invokes that adapter over JSON stdin and requires {text,tools_used:[]} JSON output. The adapter must enforce tool denial; returned attestation is not proof of a secure sandbox and independent review remains required. tools/chinese_author.py is only an explicit optional Ark-specific route; never select it for another configured provider.
