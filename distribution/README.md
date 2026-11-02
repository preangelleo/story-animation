# Story Animation · 广场分发包

这里是 **分发包目录**，包含 manifest.json、params.schema.json 和 scaffold/。这里没有 bin/，不能在本目录直接运行安装后的命令。广场安装器会将 scaffold/ 的内容写入接收者选择的独立 Story Animation 工作区，并为 Codex 生成十二个角色。

安装后进入那个独立工作区，才运行：
```sh
python3 bin/story-animation doctor
python3 bin/story-animation init
python3 bin/story-animation ceo plan --production-date 2030-01-07
python3 bin/story-animation schedule --production-date 2030-01-07
python3 bin/story-animation research check --material-dir examples/materials
```

本地只读预览可用 `python3 scaffold/bin/story-animation doctor`；不要在分发包的 scaffold/ 内制作真实作品或运行 init，以免将私人状态混入待发布包。完整安装后说明见 scaffold/README.md、AGENTS.md、SKILL.md 及 documentation/PROVIDERS.md。

研究/核心思想→可配置作者→独立审稿→角色/独立镜头→配音/ASR/原稿FA→语义字幕→最多三视频→声音缓存→完整scene章节合成→烧录/时钟QA→主人审阅→可选独立频道发布。26画风、星期系列和章节全部参数可改。

字体二进制、个人克隆声音、API密钥、原作品/账本/客户数据不分发。原S1纯音效受供应商孤立音频再分发限制，仅带生成配方；初始静音，接收者用自己的音效配置启用。默认不启动自动任务，不继承原作者账号、制作预算或发布批准。

1.1新增：最终FA字幕的简/繁/英/日/韩可切换轨；保留review_only原集的独立pilot发布（private→封面→字幕→列表→显式privacy）；Codex gpt-6.1-sol专职thumbnail-designer。安装后阅读documentation/playbooks/，并显式运行python3 tools/configure_roles.py --execute配置本工作区角色。需要自己的force-ssl字幕OAuth授权；不继承制作方账号/授权/媒体。

1.2.0 首帧封面：正式合成前预制并核验与 final canvas 等大的封面；以封面替换第 0 帧，随后进行 UltraSafe、字幕烧录和时钟 QA。总帧数、音轨与字幕时间轴保持。提供首帧封面；各客户端自行决定预览取帧。新流程见 scaffold/documentation/playbooks/thumbnail_first_frame.md。


1.2 optional learning: SQL full-script facts, real embeddings/outbox, compact independent duplicate advice, comment intake/reply and topic ideas default off. After installation use `modules status` and `modules configure`; see scaffold/documentation/LEARNING.md. Authors may use Codex/Claude/Ark/custom providers; no vendor or learning subscription is mandatory.


1.2.2 独立候选（未发布）：正常 pilot 发布/回读/恢复/最终可见性确认零 captions.download。保留本地质量/hash、成功 caption ID 回执、实时 serving 元数据和 unknown 不盲重发合同；content_matches=null，不冒称远端全文已比对。仅人工显式 --diagnostic-caption-download 诊断可下载。1.2.1 无损画面拼接、精确章节卡帧字幕遮罩、安装者显式字体合同全部保留。详见 scaffold/documentation/playbooks/pilot_release.md。
