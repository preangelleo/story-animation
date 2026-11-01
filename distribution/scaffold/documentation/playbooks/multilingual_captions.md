# 多语言外挂字幕合同

默认支持简体中文、繁体中文、英语、日语、韩语可切换字幕；不重做声音或画面。

唯一实现 `storyflow/captions_multilingual.py`，入口 `python3 bin/story-animation multilingual`。示例：

```sh
python3 bin/story-animation multilingual prepare --source FINAL_DELIVERY.srt --chapter-gaps gaps.json --output packet.json
python3 bin/story-animation multilingual render --packet packet.json --output tracks-v1 --translation en=en.json --translation ja=ja.json --translation ko=ko.json
python3 bin/story-animation multilingual validate --manifest tracks-v1/tracks.json
```

prepare默认五轨，可用`--languages zh-Hans zh-Hant en ja ko`选择受支持子集。gaps.json是按序的`[{"start":20.0,"end":21.0}, ...]`；数值为合成示例，实际章节边界必须来自本集交付时轴。所有文件写新版本目录，拒绝覆盖。缺译稿或OpenCC时如实报缺口，不生成占位翻译。

交换稿：

```json
{"schema":"qy.caption.translation.v1","language":"en","source_sha256":"源SRT完整SHA256","groups":[{"source_ids":[1,2],"text":"Actual translation"}]}
```

翻译输入包含完整带ID的正文，不依靠孤句机器猜测。groups必须完整、按序覆盖所有源cue一次；每组1–4连续cue，不能跨章卡。简中原文和毫秒时轴逐cue保持；繁中只OpenCC s2t转换。外语每组start为首cue.start，end为尾cue.end，不累计挪时、不估算新FA。章节图像量化30fps，原FA交付cue在卡尾可有不到一帧（33.34ms）的已知边缘重叠，packet显式记录，不允许实际跨越整张卡。

技术硬限：每轨非空、最多2行、每组不超过160个非空白字符；en最多30字符/秒，ja/ko最多24字符/秒；持续至少299ms。它们是拒绝明显无法阅读的技术上限，优质字幕目标由编辑按语义和实际语言另审，不把上限当建议密度。译文遗漏/增加/专名或金额错误必须修，原件和改稿全部保留。

tracks.json绑定packet、源SRT、实际译稿与全部输出SRT SHA和source_ids映射。验收重新加载实际文件，从源时码与译稿推导每条输出，验证不是仅“同一个文件自己算SHA”。尚需独立编辑审查译文，不伪签外国母语听审。

发布使用无烧录字幕且保留已审转场/最终音轨的母片，五轨可切换；原主人交审烧录版及其旧SHA保留。字幕语言上传用zh-Hans/zh-Hant/en/ja/ko，每轨isDraft=false，不调用YouTube自动听写或sync。
