# 生产闭环
配置唯一来源为 documentation/configuration.json、config/styles.json、config/weekly_series.json。

制作日 D 对应东京日历 D+2 的 20:00。按发布日期星期选内容系列与默认视觉，显式 style-index 只改视觉不改系列，显式 series 有独立内容归属。26 个 S001—S026 索引可增改；其他故事归周日需说明分类理由。

运行入口：`python3 bin/story-animation --help`。`ceo plan --production-date YYYY-MM-DD` 返回角色与工序，`workflow next <id>` 从本地事务库返回同集下一缺口。`episode create --review-only` 用于样片，永远不能 ship。

材料包每集独立目录，含 dossier.md、themes.json（themes 1—3条，takeaway）、sources.json（sources列表，url/accessed_at/classification）。用 `research check --material-dir ...` 验证。研究角色有检索工具；作者角色只接冻结材料和新 brief，不搜索、不改旧稿。模板见 examples/materials。

大纲 outline、structure 先过 outline-review，再写纯旁白脚本 TXT，不将标题、来源、字数、自评和导演说明送配音。`artifact register` SHA 贯穿脚本/审稿/媒体。`review record` 需要不同于作者的 reviewer 和真实证据。字段及失败原因由 CLI 与 tests/test_core.py 的合成夹具展示；夹具永不能当实际审稿。

`voice` 保留分块输入与供应商回执；未知请求不重发。`production asr` 为本地 faster-whisper（用户预先下载模型），`asr-compare` 比较全文，独立编辑记录实际差异结论后 `align` 用原稿与干声对齐，不用ASR结果当字幕文本。语义 caption_units 必须覆盖全文；去标点和短句合并走唯一 smart_caption 逻辑。

角色定妆含年龄服装与表情视图，随后 scenes 中逐镜写参考 SHA 和真实生成输入；原创图使用内置 image_gen，第三方文生图仅在用户显式配置授权后。画面全幅、静图静止；无意重复由像素与SHA去重拒绝。最多三段关键剧情视频，模型/版本由用户参数选定，不保证账户可用。

声音 `sound get` 从 sound-effects/ 按 lowercase snake_case 精确命中并校验SHA；不存在才经预算与 execute 生成。不同命名版本可并存；损坏库/unknown账本不自动重做。实际音量需人听，深夜故事转场以弱音量为默认意图，勿做突发响声。

章节由语义设计，默认1秒、最长2秒为编辑建议；约3—5分钟仅参考。用 `chapters render --help` 设置标题、章序、双字体、背景/文字/线条颜色、布局/位置/间距、时长、尺寸、声音/音量/mute。A+C1默认布局配色保留；艺术字体由用户自带有权使用文件，默认缺失应报错，不默默替换。模板首次给使用者看定稿后复用。

render 将每个完整 scene 持久化为独立 CFR30 clip，转场也独立；chapter-revision 基于 scene边界 typed EDL，禁止切拼旧master的scene中段。scene_assembly 全帧像素序列证明含转场与其前后镜头，CRF0 card carrier 防止解码上下文造成闪回/卡像变化。

burn 归零 PTS、30fps、48k音轨、15360视频时钟；默认白字20/深灰95%框，不在底部造全幅带。全SRT字体覆盖预检、源输入/输出SHA与编码证据保留。clock-qa 首中尾PCM相关、时间戳、全解码、字幕边界截图；独立编辑实际看截图和整片。素材正确不等于观众喜欢，使用者最终审核单独记录。

发布默认 dry run。使用自己的 OAuth profile/token 和明确 channel ID；上传前 whoami，视频状态/定时时刻回读。review_only 无论是否被人接受都不可上传，正式集另建。不要注册自动任务直至使用者批准和首次真实发布已验。table（SQLite）管事实与成本，vector检索只提候选，不跨频道数据。

## 1.1 审阅样片的独立发布与多语
正常review_only终身禁ship不变。用户接受样片并另行明确上传授权后，可用pilot-release建立独立publication合同，绑定同一已审无烧录母片/音轨和主人实际看过的烧录片SHA；不改原集模式和账本。多语言来自最终delivery.srt冻结FA时轴，简体逐cue、繁体OpenCC、英语/日语/韩语真实译稿按1—4连续cue覆盖，不能跨章或自动改时轴。阅读性与人名金额独审后上传，五轨serving前不能公开。见documentation/playbooks/multilingual_captions.md与pilot_release.md。

封面由专职thumbnail-designer使用Codex gpt-6.1-sol内置image_gen；本集风格、角色参考、色卡、字体气质、标题/比例均参数化。先选传播命题，实际核320/160px小图；导出使用tools/export_cover.py，不裁横图冒充竖构图。没有Codex/image_gen则报告能力缺口，不换付费API。规范与独立技能随包提供，模型角色需tools/configure_roles.py --execute显式本地配置。
