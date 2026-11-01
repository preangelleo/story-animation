# 字体与 C1 设计
本频道样片 C1：标题「字制区喜脉体」，章序「庞门正道细线体」。另外候选「仓迹高德国妙黑」，字幕「爱点风雅黑长体」。它们的二进制再分发许可证尚未核定，因此本包不携带 TTF/WOFF，也不自动下载；使用者自行取得有权使用版本。

将自己的两款艺术字体放 assets/fonts/title.ttf 与 number.ttf，在 default-preset.json 设置真实 family；也可给 chapters render 传绝对路径覆盖。缺字体会明确报错，不能默默落到默认系统字体。C1 是可编辑参数组合，不要求所有使用者使用同一品牌。字幕字体单独由 STORY_SUBTITLE_FONT / STORY_SUBTITLE_FAMILY 指定；Fontconfig覆盖预检与实际选择必须一致。

原弱 S1 音效的生成配方见 examples/chapter-sound-recipe.json；ElevenCreative Terms Exhibit 4 §2 不允许把该原音效作为孤立文件/音效库在此分发，因此公共 preset 初始静音，使用者生成或配置自己的音效后 --no-muted 启用，并先听审弱音量。
