# 缩略图设计协议

专职角色 thumbnail-designer 使用 Codex gpt-6.1-sol 与内置 image_gen。封面需要兑现本期故事，不把视频第一帧或字幕截图当成设计。输入参数由每集 publication/thumbnail/parameters.json 独立保存；频道色卡、字体风格、字号、布局、标题文案、参考人物、画风、比例都可替换。

1. 先读冻结脚本、主题和真实结局，列2–3个不同传播命题，选择最能概括冲突的一项；未做流量实验时仅称编辑选择。
2. 从 style_snapshot 读取本集风格；历史集可以 production/visual-manifest.json/style_id 为冻结来源。每周风格索引只是未来默认，不能覆盖已冻结集。品牌六色与艺术字体气质是可覆盖默认，不要求所有画面只有六种像素颜色。
3. 一个主焦点、最多一个辅助线索。中文默认4–10字、1–2行，粗笔艺术标题但必须可读；画面和视频标题互补。人物参考先实际查看，再随生成请求传入。插画须明确概念重建，不伪造真实照片或未发生场面。
4. 保留外缘呼吸与右下时长区域，删除小字和日期。主角、关键字、结果数字不可被遮挡；不靠夸张惊吓表情、血腥或假财富承诺。
5. 用内置 image_gen 生成，保留完整 prompt、参考路径/来源/SHA、原图和导出。YouTube为16:9；默认JPEG1280×720、不超过2MB以兼容API，源图不无意义放大。此为保守交付约束，不把它冒充当前网页上传上限。
6. 对最终像素逐字核字，320px和160px宽实际显示做 glance test；记录真实截图与判断，不能仅凭生成prompt或DOM有img就PASS。不能看清则减字/改构图重生成。
7. 核对内容兑现，CEO独立审图片与技术数据后上传。上传先video→thumbnail→captions，并最终回读；不要修改已批准的片内图、音轨或字幕。

## 参数合同

建议字段：schema_version、episode_id、video_sha256、style.source、style.id、character_references（path/sha256）、headline_lines、supporting_clue、composition、palette、typography（named_font_style或真实字体文件；生成图不可谎称使用某个精确字体文件）、duration_safe_zone、ratio、output、small_preview_widths、tool、additional_tool_cost_usd。参数不得携带授权令牌。生成工具对颜色/艺术字体是视觉指导；只有后续确定性字形排版才可称精确字体渲染。

## 依据（2026-11-01实际查阅）

- [YouTube官方：标题与封面](https://support.google.com/youtube/answer/12340300)：简单清楚、字体可读、针对观众、标题准确；不将官方统计当作本频道流量保证。
- [YouTube官方：自定义封面](https://support.google.com/youtube/answer/72431)：16:9、JPG/PNG。当前页面建议3840×2160，最低宽640；网页与手机大小上限不同。本工作流1280×720属于兼容性默认，并非声称当前官方推荐仍为1280。
- [YouTube API thumbnails.set](https://developers.google.com/youtube/data/reference/rest/v3/thumbnails/set)：发布前以API文档媒体限制核验，采用小于2MB的保守文件。
- [Paddy Galloway本人访谈及逐字稿](https://creatorscience.com/podcast/paddy-galloway/)：先想值得点击的命题，短暂扫视能理解，不同方案要有不同概念而非只换衣服颜色。中文4–10字与320/160px检查为本项目实践，不冒充该创作者定律。

