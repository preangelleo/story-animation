---
name: story-pilot-release
description: 主人接受历史review-only成片后，依新明确发布指令建立独立pilot发布记录并按视频、封面、字幕、系列列表顺序提交，不修改旧episode模式
---
# 已接受样片独立发布

读 `documentation/playbooks/pilot_release.md`。只适用于主人已接受且另行授权上传的真实作品；不因为旧技术QA通过或平台账号已接入就推定允许公开。

不得把原review_only改false、复用普通ship绕旧门或改旧历史账本。`qy pilot-release create`读取新发布合同，绑定旧源报告/两路审查、主人接受原文、新上传原文、无烧录同音轨母片、缩略图、五轨字幕与元数据SHA。初始private；独立publication计划留存在原集的publication子目录。

每步显式profile+预期channel，禁止跨频道fallback。依序upload→thumbnail→captions→playlist→privacy；默认dry-run，真实操作加--execute。先确认OAuth含youtube.force-ssl（或合作方youtubepartner）字幕权限。最终public/unlisted必须依据本次明确要求显式选；等待主人选择时可完成private准备与上传。

网络写之前先保存unknown意图；结果未知或任何在途unknown不再POST。readback只读，recover只针对上传、字幕、列表、可见性核查；错误字幕还须下载对比全文时码。缩略图未知保留待人工核查，不把默认首帧图当自定义封面成功。回读五字幕serving及唯一系列归属后才改最终可见性，逐项保存真实回执，不用退出码/页面提示冒充发布。
