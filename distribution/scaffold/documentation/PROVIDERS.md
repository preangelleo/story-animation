# 独立安装与供应商
Python 3.11+、ffmpeg/ffprobe、Fontconfig。将 requirements.txt 装入你自己的 Python 环境；本包不下载或复制制作方的环境。无需任何 ~/bin 私有脚本。

先执行 `python3 bin/story-animation doctor`。离线 init/schedule/素材冻结/审稿/样式解析/缓存检查可直接用；生产依赖缺失时 doctor 明确 production_ready=false。

ElevenLabs：包内 adapters/el.py 支持 speak、align、sfx、music，直接调用官方 API，凭据仅读当前进程的 ELEVENLABS_API_KEY；声音只用使用者的 ELEVENLABS_VOICE_ID。模型可配置，默认 eleven_multilingual_v2，不假定有新模型权限。每次付费调用由上游 begin/预算/confirm-cost 控制，不自动重试。切勿把 key 或 voice ID 写进公开包；在 Agent J 密钥卡/自己的秘密管理器中配置环境。
参考 https://elevenlabs.io/docs/api-reference/text-to-speech/convert ，https://elevenlabs.io/docs/api-reference/forced-alignment/create ，https://elevenlabs.io/docs/api-reference/text-to-sound/convert ，https://elevenlabs.io/docs/api-reference/music/compose 。

可配置作者：先用 modules configure --author-provider 选择 Codex、Claude、Ark 或自己的 provider。实际 author CLI 消费该配置：未配置 author.command 时生成独立、禁工具写作的冻结交接包；配置安装者自有 command argv 后以 JSON stdin/stdout 调用该适配器。见 LEARNING.md 的 Configured author execution。Ark 仅为可选路线，需另行安装 OpenCode、配置 ARK_AGENT_PLAN_API_KEY 后显式运行 tools/chinese_author.py；它不代表默认或必需供应商。费用、订阅与工具权限由使用者核验；不自动改为按量付费。

图像：优先 harness 内置 image_gen（角色参考图必须实际附带）。本包保存生成输入、SHA、参考和独审，不携带任何人的图片/声音。没有 image_gen 时 doctor/CEO 必须如实报告并让使用者选择可用能力。

视频：adapters/replicate.py 调官方 prediction REST，REPLICATE_API_TOKEN 仅环境读取。documentation/configuration.json 的 content.video_inserts.default_model/model_version 是可编辑参数；公开默认模型/version只是参数值，不保证该模型可用、价格或账户权限；上线前核供应商目录，配置自己的合法模型/version。提交一次后仅GET轮询；超时保留unknown。

HyperFrames：安装 Node/npm，预先 `npm exec --yes --package=hyperframes@0.8.225 -- hyperframes --version` 缓存并安装所需浏览器依赖，chapters render 使用固定版本 --offline，字体用真实本地文件并预检全字形。本包不包含艺术字体二进制，见 FONTS.md。

YouTube：安装 google-api-python-client/google-auth；先在自己的 Google Cloud 项目为独立频道完成 OAuth，token放 STORY_YOUTUBE_TOKEN_DIR/profile.json（默认用户配置目录）。adapters/yt.py 和包内服务只读取你的单独 profile；回读频道身份后才能提交。无浏览器登录/授权自动化或私人默认账号。

交审：本地文件可直接观看；可选设置 STORY_REVIEW_UPLOAD_COMMAND 为自己的上传器（参数列表，不经过shell），打印 URL: https://...。这只是用户选择的公开URL上传出口，先确认上传目的地和费用。本包无存储凭据或制作方域名。回读GET检查大小SHA。

字体字幕：设 STORY_SUBTITLE_FONT 为真实文件、STORY_SUBTITLE_FAMILY 为其Fontconfig PostScript家族、STORY_SUBTITLE_FONT_INDEX 为字体集合中的真实face index（默认0）；不匹配或缺字拒绝。默认沿用字框样式；可在工具参数和配置中扩展。实际姓名/金额/韵律由使用者听审，不冒称机器主观听过。

公开的音效库功能是在各使用者本地积累生成结果，不包含制作方生成的音效二进制。原S1提示词与弱音量意图保留；官方 ElevenCreative Terms Exhibit 4 §2 限制孤立音效再分发，因此不要把本地声音库原文件作为公共资源包上传。

1.1字幕API：YouTube OAuth必须由使用者事先授权youtube.force-ssl（或合作方youtubepartner）；仅upload/readonly不足。包内factory会检查显式profile和已有token的scope记录，缺失即拒绝，不会自行追加scope、刷新授权或写凭据。重新授权须使用者明确同意；本包不含任何频道/列表/合同/令牌。

1.1封面：执行python3 tools/configure_roles.py --execute，只配置当前安装工作区的thumbnail-designer模型为gpt-6.1-sol；不会修改全局Codex配置。广场生成角色本身不保留model字段，因此不能只相信Markdown角色已存在。doctor区分配置模型与实际内置image_gen能力，后者由CEO在真实Codex会话核实。
