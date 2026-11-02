# 字体与 C1 设计
本频道样片 C1：标题「字制区喜脉体」，章序「庞门正道细线体」。另外候选「仓迹高德国妙黑」，字幕「爱点风雅黑长体」。它们的二进制再分发许可证尚未核定，因此本包不携带 TTF/WOFF，也不自动下载；使用者自行取得有权使用版本。

将自己的两款艺术字体放 assets/fonts/title.ttf 与 number.ttf，在 default-preset.json 设置真实 family；也可给 chapters render 传绝对路径覆盖。缺字体会明确报错，不能默默落到默认系统字体。C1 是可编辑参数组合，不要求所有使用者使用同一品牌。字幕字体单独由 STORY_SUBTITLE_FONT / STORY_SUBTITLE_FAMILY 指定；Fontconfig覆盖预检与实际选择必须一致。

原弱 S1 音效的生成配方见 examples/chapter-sound-recipe.json；ElevenCreative Terms Exhibit 4 §2 不允许把该原音效作为孤立文件/音效库在此分发，因此公共 preset 初始静音，使用者生成或配置自己的音效后 --no-muted 启用，并先听审弱音量。

## 安装与字体选择预检

只有字体文件路径还不够：Fontconfig 必须在运行工作流的同一用户/容器环境中发现该字体，且 family/PostScript 名称和 face index 与配置一致。将自己合法取得的字体安装到该用户字体目录后运行 `fc-cache -f`；或提供显式的本地 Fontconfig 配置。先检查 `fc-scan <font-file>`、`fc-query <font-file>` 和 `fc-match -f '%{file} %{family} %{postscriptname} %{index}\n' '<family>'`；选中字节或名称不符就停止制作，不用 fallback 掩盖。

例如隔离 HOME 测试可在临时目录建立 fonts.conf，内容为 `<fontconfig><include>/etc/fonts/fonts.conf</include><dir>/absolute/path/to/your/font-directory</dir><cachedir>/absolute/path/to/temporary-font-cache</cachedir></fontconfig>`，然后仅为本次命令设置 FONTCONFIG_FILE。路径由安装者自己的环境决定，字体和缓存均不随包分发。设置 STORY_SUBTITLE_FONT、STORY_SUBTITLE_FAMILY、STORY_SUBTITLE_FONT_INDEX 后重新执行实际字体预检与小段烧录；doctor 的一般依赖就绪不能替代这项实际选择检查。
