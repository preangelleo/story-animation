# Story Animation · 动画故事工作流

[English](README.en.md) · [官网介绍](https://agentj.app/workflows/story-animation/) · [制作流程](distribution/scaffold/documentation/WORKFLOW.md) · [供应商配置](distribution/scaffold/documentation/PROVIDERS.md)

把一个值得讲的故事，变成有旁白、角色一致画面、字幕和章节的完整视频。这个开源工作流供 Agent J 与你的 AI 助手编排；你保留选题、预算、素材权利和发布决定。

## 看一个真实作品

[![观看张子强故事试播集](https://p.preangel.me/workflows/story-animation/v1.1.0/poster.jpg)](https://p.preangel.me/workflows/story-animation/v1.1.0/showcase-zh.mp4)

[直接观看永久视频](https://p.preangel.me/workflows/story-animation/v1.1.0/showcase-zh.mp4) · [YouTube 备选](https://youtu.be/j7upT3jbVDo) · 约 18 分钟。复古弹性卡通风，真人授权克隆旁白，关键剧情视频、轻音效和章节。R2 视频提供简体中文烧录字幕；YouTube 备选提供简繁英日韩五轨可切换字幕。它展示经人工验收的制作结果；作品媒体不属于代码的 MIT 授权，不能据此克隆声音或再分发素材。

## 复制给 Agent J

> 请从 Agent J 广场查看工作流 **Story Animation**（ID：`story-animation`，版本 `1.2.3`）。作者可选Codex、Claude、Ark或我自己的适配器；全部学习模块默认关闭，逐项说明SQL事实记忆、真实embedding/outbox、独立查重、评论与选题的前置和费用；默认只读预览评论回复，未经我显式启用不发送。阅读清单、README、技能和供应商要求，在我选择的独立新目录预览安装；说明所需权限和未验证状态，由我按原生安装流程确认。逐项检查我已有的模型、图像、配音、强制对齐、字体和渲染能力；密钥只使用密钥卡或我认可的秘密管理器。把已验证能力与需适配的替代供应商区分清楚，先给出配置报告、选题研究计划和成本预算，等我批准再付费制作。不要购买套餐、继承其他频道账号、自动发布或开启定时任务。参数不能满足的个性化需求，请先提出代码修改方案。

## 安装与本地预览

广场的完整下载需要具有付费席位的账户；这个 GitHub 源码可公开查看与克隆。第三方服务费用另计。广场安装命令默认先预览，不代表已执行安装：

```sh
agentj plaza install story-animation --version 1.2.3 --workspace /your/new/story-workspace --harness codex
```

替换示例路径，使用 `--help` 查看本机安装器的确认、权限与 digest 流程，不跳过校验。社区包的状态不等于官方认证。

本仓库保留完整的广场分发封装在 `distribution/`，不会把它冒充已经配置好的工作区。离线只读体检：

```sh
git clone https://github.com/preangelleo/story-animation.git
cd story-animation
python3 distribution/scaffold/bin/story-animation doctor
```

不在 `distribution/scaffold/` 内运行真实制作。由 Agent J 把 scaffold 安装到独立工作区，或人工复制到自己的新工作区，阅读其中 `AGENTS.md` 后配置依赖。`doctor` 会如实报告缺口；离线计划成功不等于可以生产。

## 经过验证的组合与替代方案

| 能力 | 首个验收版本采用的组合 | 替换的边界 |
|---|---|---|
| 编排与图像 | Codex；内置 `image_gen`，角色参考图；封面角色 `gpt-6.1-sol` | 其他 harness 或第三方图像服务需要配置适配器，并重新验证参考图与交付合同 |
| 中文创作 | 1.1.0试播集使用OpenCode + 方舟Agent Plan `doubao-seed-2.1-pro`；1.2.3读取自配author.provider | Codex/Claude/自选作者可用独立禁工具交接或自有适配器；认证、费用与质量须验证，不自动转按量计费 |
| 旁白、强制对齐、声音 | 自有 ElevenLabs API 与授权声音；试播集 `eleven_v4`，公开默认 `eleven_multilingual_v2` | Fish Audio、火山等可作为适配方向；当前包不保证它们开箱即用，也不把识别字幕当原稿强制对齐 |
| 渲染 | Python 3.11+、ffmpeg/ffprobe、Fontconfig；Node、HyperFrames 0.8.225 与浏览器 | 艺术字体必须由使用者提供并拥有使用权 |
| 关键剧情视频 | 试播集 Seedance2.5，包内可选 Replicate 接口，每期最多三段、约十秒 | 模型/version 是参数；账户权限、价格和实际可用性需制作前核查 |
| 发布 | 可选 YouTube 独立 OAuth profile、channel ID；字幕需要 `youtube.force-ssl` | 不默认发布，不共享制作方账户；授权可撤销，不能承诺永久不变 |

声音相似度、姓名读音和节奏由使用者听审。授权声音、密钥、字体、作品、私有数据库和生成音效库不随源码分发。

## 不改代码可以调整什么

- **内容与视觉**：语言、时长目标、每期主题、七天系列与 26 种画风索引；可编辑 JSON 添加自己的风格。
- **品牌与排期**：名称、频道、时区、发布时间与提前制作天数；默认 D+2 东京 20:00，仅是参数，不会启动自动任务。
- **模型与预算**：作者模型/端点、声音模型/声音、视频模型/version、插入数量/时长、单集/月度预算。数值不是支出批准。
- **字幕与封面**：自有字幕字体/字体集合 face、简繁英日韩字幕轨（当前字号与字框颜色是代码定义，不能声称通用 CLI 可调）；封面角色、风格、标题、比例与参考图。
- **章节**：标题、章序、双字体、布局、位置、间距、颜色、线条、尺寸、时长、音效、音量或静音。
- **存储与声音**：本地数据库、素材与检索命名空间；小写精确命名的本地声音缓存，命中复用，缺失才经预算批准生成。

配置入口：[configuration.json](distribution/scaffold/documentation/configuration.json)、[styles.json](distribution/scaffold/config/styles.json)、[weekly_series.json](distribution/scaffold/config/weekly_series.json)。安装向导当前仅暴露 `project_name`；其余由安装后的 JSON、环境变量及各命令 `--help` 中的真实参数配置。参数不能满足时，源码、角色和技能均可修改；请保留预算、隐私、事实来源与独立审稿边界，并重新测试。

## 制作顺序

研究与证据 → 提炼 1–3 条核心思想 → 冻结材料 → 从零写纯旁白 → 独立编辑 → 角色定妆与镜头 → 配音与原稿强制对齐 → 语义字幕 → 可选剧情视频与克制的声音 → 完整场景与独立章节 clip 顺序合成 → 人工验收 → 另行授权发布。

静态画面保持静止；章节只插在完整场景边界。字幕来自原稿强制对齐，识别结果用于检查。真实事件应区分证据、归因引语与戏剧化，不把创作段落冒充史料。

## 验证、贡献与许可

```sh
cd distribution/scaffold
python3 -m unittest discover -s tests -q
```

测试使用合成数据，不能替代真实供应商与人工验收。请读 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md)、[LICENSE](LICENSE) 和保留的 [NOTICE](distribution/scaffold/NOTICE.md)。MIT 覆盖项目自有源码与文档；第三方服务、模型、字体、媒体、商标及来源内容各受其自身条款约束。


## 1.2.0 可选学习闭环

SQL最终稿事实记忆、真实embedding/outbox、独立查重、评论收集/回复和反馈选题均可选，默认全部关闭；SQLite制作安全账本保留。安装后运行 `modules status`、`modules configure --help`、`author --help`，完整参数见 [LEARNING.md](distribution/scaffold/documentation/LEARNING.md)。Codex/Claude/自选作者可用独立禁工具交接包或自有适配器，不强制Ark。语义服务未配置或未实际生成时只报告SQL/词面能力，不声称已有语义库。原试播片仍是1.1.0样例，无新媒体重制。


## 1.2.1 拼接与章节字幕补丁

拼接先独立解码并使用无损载体，逐帧验证输出保持原场景画面。正常章节烧录按绑定的 typed EDL 帧区间遮罩正文字幕，避免旧字幕在章节卡首尾残留；不改原始 SRT、强制对齐或五语轨。字幕字体须由安装者合法取得并显式设置 `STORY_SUBTITLE_FONT`、`STORY_SUBTITLE_FAMILY`，集合字体按需设置 `STORY_SUBTITLE_FONT_INDEX`，并通过 Fontconfig 注册和实际预检；不分发字体，也不预设私人字体。335 项实际安装合成/mock测试与第二字体9项通过，只证明安装和技术回归；不是新供应商、真实语义检索或新影片的验收。
