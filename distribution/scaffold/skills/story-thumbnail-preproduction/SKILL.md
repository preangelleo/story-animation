---
name: story-thumbnail-preproduction
description: 正式合成前冻结 final canvas 封面合同并协调专职 Codex 设计、prepare/verify/consume
---
先读 documentation/playbooks/thumbnail_first_frame.md 和 documentation/PARAMETERS.md。thumbnail-preproducer 冻结本集脚本/风格/角色来源与最终 canvas，委派现有 Codex gpt-6.1-sol thumbnail-designer 使用内置 image_gen；不得用导出工具替代生成及实际 320/160px 审查。

先完成等大封面与独立审查，再执行包内 thumbnail prepare/verify；媒体 consume 后才能进入 UltraSafe/字幕烧录/clock QA。缺失、尺寸/比例/SHA/引用不符、旧 receipt 拒绝。仅替换第 0 帧，不改变总帧数、片长、音频 PTS 或 cue 时钟；字幕第 0 帧禁止，从第 1 帧恢复原窗口。保留新 receipt、frame0/frame1 与首中尾时钟证明。

只写本次独占目录；不更改历史作品/授权，不产生付费调用，不上传。平台上传 JPEG 与 final-canvas 封面独立导出、各记 SHA。
