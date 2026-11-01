---
name: story-captions-multilingual
description: 将已审核的原稿强制对齐字幕制作成可切换的简中、繁中、英语、日语、韩语外挂轨；冻结来源与覆盖关系，不重新识音
---
# 多语外挂字幕

使用 `qy multilingual prepare/render/validate`，完整合同见 `documentation/playbooks/multilingual_captions.md`。默认五轨可用 `prepare --languages` 显式选择；默认语言集合是参数，独立pilot五轨门须满足发布合同。

prepare 的 source 必须是最终章节插卡后的 delivery.srt，不是插卡前干声FA SRT。原稿、音频、FA和源字幕不得改写。简中逐cue保持；繁中用OpenCC s2t；英语/日语/韩语交独立翻译者阅读完整带ID的冻结材料，真实翻译不总结、不生造。翻译者可将1–4个连续cue合组，不跨章节；原音频FA毫秒与30fps卡片边缘最多1帧容差明确落档，不累计挪时间。

译稿必须包含source_sha256、language和全覆盖一次的source_ids。render将真实译稿按源cue首尾派生SRT，并保存版本绑定。validate检查全覆盖、来源、文件哈希、分组、时轴、长度和阅读速度；技术通过不等于译文质量，通过独立编辑逐段检查遗漏/人名/数字/章序后再上传。新语言不是重跑ASR的理由。
