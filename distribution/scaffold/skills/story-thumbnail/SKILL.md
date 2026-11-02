---
name: story-thumbnail
description: Story Animation每集封面，Codex内置生图、单集风格参数与小图可读性验收
---
# 单集缩略图

由 thumbnail-designer（Codex gpt-6.1-sol）执行。仅使用本包协议和当前Codex内置image_gen；先读 documentation/playbooks/thumbnail_design.md。输入：冻结脚本、核心思想、最终片身份、单集风格快照、角色定妆图、品牌参数、目标平台/比例和独占输出目录。没有快照时读取本集 production/visual-manifest.json 的 style_id，不能根据制作日或默认频道风格猜测。

输出包含 parameters.json、完整 prompt、原图、YouTube JPEG、来源及 SHA 清单、320/160px 实际显示截图和人工视觉判断。修改参数重新生成，不写死本期文案/角色/风格到通用模板。不用图片处理脚本替代生成；尺寸与格式导出只用已落地 tools/export_cover.py。确认中文逐字正确、人物一致、右下时长不挡关键内容，承诺真实兑现；没有流量实验不宣称 CTR 胜出。

付费外部引擎不在默认范围；内置订阅工具新增费用记录0，不把它写为真实 API 支出。无工具报告缺口，不偷偷换收费API。生产与上传分开，CEO 先拿video_id，再调用缩略图上传，保存回执并读回实际远端图。未知 POST 先回读不盲重试，不触碰其它视频和账号。

正式合成前由 thumbnail-preproducer 提供最终 canvas 与冻结参考。先产出等尺寸首帧封面，再单独导出平台上传版；两者各有 SHA。设计和实际小图审查完成后交 prepare/verify；生成工具、模型和既有职责保持。见 documentation/playbooks/thumbnail_first_frame.md。
