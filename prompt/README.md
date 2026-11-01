# 生产模板

三个子目录对应三个生产模块，文件名统一为 `<模块>pipe_<用途>.txt`。模板提供通用写作、组装或审核规则；具体作品由原文、`--notes`、季设定、分镜和图片清单传入。

## script：12 个

写作和审核模板作为 system prompt，数据通过 JSON payload 传入；模板正文不做字符串格式化。当前 brief 决定集数、场数和时长。

| 文件 | 调用位置 / 用途 |
| --- | --- |
| [scriptpipe_short_season_plan.txt](script/scriptpipe_short_season_plan.txt) | `scriptpipe/pipeline.py`：短剧整季设定和分集计划 |
| [scriptpipe_short_episode_write.txt](script/scriptpipe_short_episode_write.txt) | 同上：单集分镜写作 |
| [scriptpipe_short_season_review.txt](script/scriptpipe_short_season_review.txt) | 同上：整季文字审核 |
| [scriptpipe_short_shot_repair.txt](script/scriptpipe_short_shot_repair.txt) | 同上：指定镜头局部修正 |
| [scriptpipe_long_season_plan.txt](script/scriptpipe_long_season_plan.txt) | `scriptpipe/long_pipeline.py`：长剧整季设定 |
| [scriptpipe_long_episode_plan.txt](script/scriptpipe_long_episode_plan.txt) | 同上：分集场次、镜号范围和剧情节拍 |
| [scriptpipe_long_scene_write.txt](script/scriptpipe_long_scene_write.txt) | 同上：按场写镜头 |
| [scriptpipe_long_episode_review.txt](script/scriptpipe_long_episode_review.txt) | 同上：单集文字审核 |
| [scriptpipe_long_season_review.txt](script/scriptpipe_long_season_review.txt) | 同上：跨集因果与连续性审核 |
| [scriptpipe_long_shot_repair.txt](script/scriptpipe_long_shot_repair.txt) | 同上：长剧镜头局部补丁 |
| [scriptpipe_export_h3_i2va.txt](script/scriptpipe_export_h3_i2va.txt) | `scriptpipe/export.py`：将分镜编译为 H3 输入 |
| [scriptpipe_json_retry.txt](script/scriptpipe_json_retry.txt) | `scriptpipe/llm.py`：JSON 解析失败后的重试说明 |

H3 编译模板使用 `{style}`、`{subjects}`、`{visual}`、`{camera}`、`{dialogue}`、`{soundscape}`；编译器把逐字中文台词放入 `<d>`，并保留人物标签和画外音要求。

## image：6 个

由 `imagepipe/plan.py` 根据槽位及引用选择，通过 `videopipe/prompts.py` 加载。使用 `{style}`、`{description}`、`{reference_guide}`、`{width}`、`{height}` 中需要的字段。

| 文件 | 用途 |
| --- | --- |
| [imagepipe_character_master.txt](image/imagepipe_character_master.txt) | 人物首套服装母图：清晰单人身份参考 |
| [imagepipe_wardrobe_edit.txt](image/imagepipe_wardrobe_edit.txt) | 保留人物身份，替换当前服装 |
| [imagepipe_location_reference.txt](image/imagepipe_location_reference.txt) | 具有固定空间特征的地点参考 |
| [imagepipe_prop_reference.txt](image/imagepipe_prop_reference.txt) | 可复用的道具外观参考 |
| [imagepipe_prop_edit.txt](image/imagepipe_prop_edit.txt) | 保留物品身份，改变所需状态 |
| [imagepipe_first_frame.txt](image/imagepipe_first_frame.txt) | 组合参考图，构成动作开始时的完整场面 |

多参考使用 `<image1>`、`<image2>` 等编号，单参考使用 “the reference image”；顺序与实际上传一致。布景锚点贡献环境，当前镜头决定出场人物、姿态与构图。尺寸和步数由 API 配置决定，执行中直接组装模板。

模板整理时沿用的上游参考：[Qwen 文生图说明](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_t2i.txt)、[编辑说明](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_edit.txt)、[ComfyUI 引用顺序](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1)、[Qwen 参数及多参考示例](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/README.md)。

## videopipe：6 个

前五个由 `videopipe/backends/review.py` 使用，最后一个由 `videopipe/inputs.py` 在保存旧包快照时使用。

| 文件 | 用途 / 格式化字段 |
| --- | --- |
| [videopipe_visual_review_system.txt](videopipe/videopipe_visual_review_system.txt) | 单镜头画面审核标准 |
| [videopipe_visual_review_user.txt](videopipe/videopipe_visual_review_user.txt) | `{duration}`、`{script}`、`{shot_specs}` |
| [videopipe_visual_review_reference.txt](videopipe/videopipe_visual_review_reference.txt) | 参考图标签：`{index}`、`{shot_id}` |
| [videopipe_visual_review_frame.txt](videopipe/videopipe_visual_review_frame.txt) | 抽帧标签：`{timestamp}` |
| [videopipe_transcript_compare_system.txt](videopipe/videopipe_transcript_compare_system.txt) | 预期对白与 ASR 文本差异 |
| [videopipe_legacy_negative.txt](videopipe/videopipe_legacy_negative.txt) | 旧负面词一次性并入有效 prompt：`{negative_prompt}` |

视觉审核模板共同组成一次请求。审核结果进入 [视频选片流程](../docs/workflow.md)，生成 prompt 由上游脚本包提供。
