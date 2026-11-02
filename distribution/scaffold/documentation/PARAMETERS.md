# 参数目录

最终合成：documentation/configuration.json 的 render.width/height 定义 final canvas；原 scene asset 编码可不同，normalized scene 必须一致。render.first_frame 默认 {"enabled":true,"frame_count":1,"fit":"strict"}，替换 t=0 而不插入时长。精确字段及 CLI 命令见 thumbnail_first_frame.md 与包内 --help。

封面设计：publication/thumbnail/parameters.json 单集可配置风格、参考、文案、字体气质、色卡、布局；参考必须含来源与 SHA。final-canvas 封面与平台 JPEG 是不同输出，分别记录尺寸和 SHA。320/160px 实际审查保留。

章节：templates/chapter-transition/parameters.schema.json；多语字幕：playbooks/multilingual_captions.md；独立发布：playbooks/pilot_release.md。预算和上传授权不会从作者继承，默认演练。

审核记录是独立的项目本地 review JSON，绑定 image_sha256、context 和 references；使用者承担来源/隐私审查，禁止把原作者审核或旧图授权当成当前集授权。CLI prepare 默认预览，--execute 保存；verify/consume 重新核验原引用。

`thumbnail context EP` 返回当前 {episode_id, context}；独立审查将 context 原样冻结到 review JSON，不得自造依赖。reviewer 不得等于本集 author。支持 context/prepare/verify/consume 四个子命令，具体选项以 --help 为准。

| 参数入口 | 默认值 | 验证/效果 |
|---|---|---|
| render.width / render.height | 1920 / 1080 | 最终画布，封面实际解码尺寸逐项相等 |
| render.first_frame.enabled | true | 新 final 默认开启；显式关闭不能声称提供了首帧封面 |
| render.first_frame.frame_count | 1 | 只支持 1；替换，不增加时长 |
| render.first_frame.fit | strict | 不裁切、拉伸或缩放 |
| review.context | 当前冻结上下文 | `thumbnail context EP` 取得 context，绑定稿/结构/风格/visual-plan |
| review.references | 显式数组 | 每项项目内 path/sha256，无引用时才为空 |
| review.reviewer / reason | 必填 | 独立审者不得为本集 author；实际来源/隐私审查 |
| final decoded RGB mean error | ≤12/255 | 实际第 0 帧解码像素容差，另需独立目视 |

代码/封面/审稿/参考/上下文/画布变化进入指纹，不能覆盖旧失败证据。新 final 必须绑定新版消费与烧录 receipt；legacy 只读兼容不等于新终审有效。

render.first_frame 的可分享 JSON Schema：schemas/thumbnail-first-frame.schema.json。分发根 params.schema.json 仅控制安装时 project_name；运行参数以本目录 configuration.json 和上述 schema 为准，不把安装参数冒充已应用的运行配置。


Optional SQL/vector/comment intake/reply/topic ideas/dedup: all off by default. See documentation/LEARNING.md, schemas/learning.schema.json and `modules configure --help`. Author provider is configurable; research stays in the orchestrator and writers receive frozen materials plus compact independent angle advice.
