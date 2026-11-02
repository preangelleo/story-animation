# 已验收样片的独立pilot发布

ADR-005：旧review_only作品创建后不升级、不改状态；主人实际接受并另行明确指令上传时，为同一物理已审媒体创建独立publication记录。源集、旧费用/失败/QA/交审记录完全保留。接受者必须提供自己本次真实接受与发布授权原文；本包不含制作方批准。这不等于日更timer、其它旧作品或其它账号的上传权限。

模块 `storyflow/pilot_release.py`，`python3 bin/story-animation pilot-release`。create消费JSON合同，必要字段：

- kind=`accepted-review-pilot`、source_review_only=true、publication_id、episode_id。
- expected_channel_id与独立profile；禁default/其它频道别名，不隐式回退。
- source_report、owner_acceptance、upload_authority（本轮主人原文）。
- accepted_burned_sha256（主人实际看过的版本）；源报告须有同集机械时钟及A/B通过，另冻结真实两路报告。
- thumbnail、captions_manifest、captions_review、metadata（实际文件路径），playlist_id。caption review必须status=pass、真实reviewer、source_sha256、tracks_manifest_sha256，checks.coverage/names_numbers/faithfulness/readability均为pass；译者不可自签独审。
- metadata必须title（1–100字符）、description（加唯一发布标识后≤5000字符）、made_for_kids=false，可配置category_id和tags。支持description_file指向metadata同目录内的UTF-8说明文件（独立SHA绑定），以及self_declared_made_for_kids=false同义字段。

视频从源报告final_media.delivery.video读取，须与chapter_clean和delivery中的SHA都相同；它是相同最终音轨/转场的无烧录母片。caption manifest源SHA须等于同报告delivery.subtitles SHA，五轨齐全。create只写新的计划；原数据库及episode.json不改。

```sh
python3 bin/story-animation pilot-release preflight --contract pilot-contract.json
python3 bin/story-animation pilot-release create --contract pilot-contract.json --output pilot-plan.json
python3 bin/story-animation pilot-release upload --plan pilot-plan.json
python3 bin/story-animation pilot-release upload --plan pilot-plan.json --execute
python3 bin/story-animation pilot-release thumbnail --plan pilot-plan.json --execute
python3 bin/story-animation pilot-release captions --plan pilot-plan.json --execute
python3 bin/story-animation pilot-release playlist --plan pilot-plan.json --execute
python3 bin/story-animation pilot-release readback --plan pilot-plan.json
python3 bin/story-animation pilot-release privacy --plan pilot-plan.json --privacy unlisted --execute
```

默认所有写操作dry-run；readback是只读远端查询，但会保存本地观察。首次视频始终private；最终public/unlisted选择依据真实主人指令，单独显式参数，不能由排期或默认值推断public。视频标题说明加入唯一publication标识，便于错误回执只读辨认。YouTube不能回读原上传文件SHA，记录的本地SHA仅证明提交源文件；不虚构远端逐字节验证。

每次写API前真实channels.list(mine=True)核唯一channel ID，逐步videos.list核channel/发布标识。调用本包providers.youtube_service(profile)，只读使用者自己的显式独立OAuth配置；不调用本机私有全局脚本，不写凭据。API captions.insert需要youtube.force-ssl（或合作方youtubepartner）作用域，youtube.upload单独不足；当前官方文档未列youtube单独作为captions.insert授权；只读列出可授予权限，缺权限由本人重新同意，不无提示扩权。

先落盘unknown再POST；成功回执持久后才继续。任何unknown阻止后续写，不能换publication_id逃避。网络异常只记安全类型，不存可能带凭据的异常全文。持久文件锁防并发。state/pilot-publications.json按channel与母片SHA登记唯一发布，换ID/新输出路径也拒绝重复上传；这是新的无秘密索引，不修改旧episode数据库。同输入done操作幂等回读；上传成功但本地最后写断电可从done结果续接，不重新传。

未知上传仅显式`recover --operation upload --remote-id ID --reason ...`只读核channel、标识、标题、处理状态及private；字幕恢复仅 captions.list 核唯一 caption ID、video/language/name 及 serving，不下载；failed 明确失败。unknown 始终只对账，不重发。列表恢复核唯一video与item ID，可见性恢复核video与privacy。缩略图无法从YouTube转码产物证明原文件字节相等，未知状态保持待人工核图，不自动假定成功或重新提交。

最终回读：video/channel/visibility；自定义缩略图提交回执及远端各尺寸URL（最终图须独立目视）；五个确切caption ID、对应语言且status=serving；唯一playlist membership。字幕可能异步processing，重复readback不重复字幕POST，全部serving之前不改最终可见性。

按内容系列选择使用者自己的playlist ID，与实际上传星期无关；不得继承原制作方列表或频道，缺列表须另行授权创建。

官方API核对（2026-11-01）：[字幕上传授权、sync弃用和参数](https://developers.google.com/youtube/data/reference/rest/v3/captions/insert)、[字幕serving状态及下载](https://developers.google.com/youtube/data/reference/rest/v3/captions)、[上传视频的状态字段](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert)。字幕上传不用sync参数，保持自有FA时轴。

## 独立接收者的公开媒体收据
source_report可用`schema=story-animation.accepted-media.v1`，样例与字段见examples/pilot/accepted-media.json。直接绑定本人的episode_id、source_review_only、accepted_burned_sha256、clean_video/subtitles path+SHA、mechanical_clock_passed，以及reviews.A/B的path/SHA/verdict/reviewer。路径相对收据，真实两个独审不同上下文；未知示例占位必须替换。此公开格式不依赖制作方私有报告、固定集号/目录或接受SHA。兼容旧本地报告只为迁移，不要求接收者获取私有文件。

修改可见性时videos.update是status部分替换；必须保留真实回读的license/embeddable/publicStatsViewable/selfDeclaredMadeForKids/containsSyntheticMedia等可写已存在字段（包括false），仅覆盖privacyStatus；不注入缺失字段、不回传uploadStatus/madeForKids等只读项。


## 1.2.2：正常流程零字幕下载

本地五语正文、时码、hash 与独立语言审稿仍必须通过。上传只接受真正成功的 API 响应及非空 caption ID；正常 readback、privacy 最终可见性确认和 recover 只消费持久成功 ID 回执与实时 captions.list，核对应 video/language/name、唯一 ID 和 serving。processing 保持等待，不改最终可见性；failed 明确拒绝。unknown、异常、403 和损坏回执不能假成功或盲重发。content_matches 为 null；upload_receipt_verified / metadata_serving_verified 不表示远端全文比对。旧成功 ID-only 回执兼容，旧全文/cache 证据保留为历史。

仅人工显式 `pilot-release readback --plan PLAN --diagnostic-caption-download` 或 `pilot-release recover --plan PLAN --operation caption:LANG --remote-id ID --reason TEXT --diagnostic-caption-download` 才下载真实远端字幕并比较 cue；普通默认不下载，后台不会自动开启。完整五语 readback 诊断下载5次，不依赖私有 scheduled_release 模块；recover 诊断按实际可验证轨执行，不能把它当正常恢复前置。传入诊断参数不能授权其它 API 写入。
