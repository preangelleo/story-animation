# 正式合成前封面与第 0 帧

封面预制作是正式合成 gate。thumbnail-preproducer 冻结最终 canvas、故事与引用，现有 Codex thumbnail-designer 使用内置 image_gen 交实际图与小图审查。机械 prepare/verify 不能代替图像设计和独立审稿。

封面尺寸必须精确等于 final canvas width/height，允许原镜头资产采用其他编码/尺寸，只要最终 normalized scene 一致。默认 enabled=true、frame_count=1、fit=strict。尺寸/比例/格式/SHA/参考审查不符时拒绝；不自动拉伸、裁切、补边，不复用不匹配的历史 thumb。

顺序：设计 → prepare → verify → 完整 scene/章节 EDL 合成 → consume 替换第 0 帧 → UltraSafe → 字幕烧录 → clock QA → 独立终审。禁止在 UltraSafe 后追加封面。第 0 帧只显示封面，字幕烧录从第 1 帧恢复原 cue 窗口；原字幕文件及所有章节时间不平移。音频 PTS、PCM/AAC 时序、duration 与总帧数保持。

receipt 绑定封面、合同、scene、参数和最终合成依赖 SHA。封面变更撤销旧缓存；重跑相同输入应幂等。QA 检查 frame0 编码容差、frame1 后原场景、CFR、PTS=0、全帧数和首中尾音频/字幕 clock。旧产物可以读取；新版 final 不接受旧 receipt 或旧审稿冒充。

提供首帧封面；微信或其他客户端的预览取帧策略由客户端决定，不承诺所有客户端显示同一帧。不得修改历史 pilot、官网或 showcase。

## CLI 合同

```sh
python3 bin/story-animation thumbnail context EP
python3 bin/story-animation thumbnail prepare EP --image episodes/EP/publication/thumbnail/final-canvas.png --review episodes/EP/publication/thumbnail/review.json
python3 bin/story-animation thumbnail prepare EP --image episodes/EP/publication/thumbnail/final-canvas.png --review episodes/EP/publication/thumbnail/review.json --execute
python3 bin/story-animation thumbnail verify EP
python3 bin/story-animation thumbnail consume EP
python3 bin/story-animation render EP --execute
```

prepare 默认预览，`--execute` 保存版本化 prepared.json 与 binding；verify/consume 只读核验。review.json 需有 episode_id、image_sha256、status=pass、独立 reviewer、reason、references_reviewed=true、references（明确 path/SHA，未使用参考时才可为空）和 context。context 绑定当前 script/structure artifact_hashes、style_snapshot、visual_plan path/SHA，必须精确匹配本集，不继承另一集批准。封面/审稿/参考必须是当前使用者项目内文件；来源和隐私权利由独立 reviewer 实际审核。

`thumbnail context EP` 返回 {episode_id,context}；将当前 context 原样交给独立审查，reviewer 不得等于本集 author。精确字段由包内 storyflow.first_frame.review_context 和 prepare 校验；外部秘密、私人授权、图片不可作为示例分发。渲染前 verify/consume 会重新核验全部依赖。
