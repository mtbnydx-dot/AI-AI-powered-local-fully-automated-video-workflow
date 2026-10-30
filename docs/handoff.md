# 脚本包交接标准

本标准用于把已有脚本包交给离线视频生产程序。线上剧本、提示词和资产制作流程保持原有职责；离线端读取提示词与资产，生成、审片、超分和合成。当前代码与配置仍需在 B200 节点完成部署验证，本文不代表已经通过真实生产测试。

## 输入与能力范围

```text
script_package/
  package.json
  script.md
  assets/
    porch-first-frame.png
    character-reference.png
```

`package.json` 是内容入口。`script` 和资产路径相对它所在目录解析；镜头顺序就是合成顺序。每个镜头需要唯一 `id` 与最终 `prompt`，并明确时长、尺寸、帧率、初始种子和画面验收要求。原生 H3 使用 24 fps、32 的倍数画布，生成帧数按模型要求对齐；媒体处理保留生成片段的实际时长，不裁到计划时长。合成清单和字幕时间窗使用实际片段时长。

| 工作方式 | 当前生成图实际接收的资产 | 可额外提供给画面审核 |
| --- | --- | --- |
| T2VA | 不接收外部参考图 | `review_only` 图片 |
| I2VA | 最多一张 `first_frame` 图片 | `review_only` 图片 |
| 多图、尾帧、参考视频、参考音频、Ref2VA | 当前内置图未实现 | 不能用审核参考图代替生成能力 |

`asset_bindings` 是可选的明确绑定列表，每项仅有 `asset_id、path、role、prompt_ref`。`role` 为 `first_frame` 或 `review_only`；`prompt_ref` 说明该资产对应的文字标识，不会让没有接线的资产自动进入模型。角色、衣服、场景等元素可以在外部提示词中描述，但单第一帧图不提供独立的多资产组合控制。

以下为自行设计的交接例子；路径需要替换为实际文件：

```json
{
  "title": "门廊上的来信",
  "script": "script.md",
  "shots": [
    {
      "id": "s01",
      "prompt": "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\nintegrated_multimodal_description: [Shot 1] A woman in a green coat stands on a quiet porch, holding a sealed envelope. The camera remains still as she looks toward the gate. She (S1) says in a calm voice: <d>[Chinese] 我会在这里等你。</d>\n\noverall_soundscape: Soft wind moves through the leaves.\n\nnon_diegetic_music: N/A",
      "negative_prompt": "",
      "asset_bindings": [
        {"asset_id": "porch-opening", "path": "assets/porch-first-frame.png", "role": "first_frame", "prompt_ref": "<Picture 1>"},
        {"asset_id": "woman-identity", "path": "assets/character-reference.png", "role": "review_only", "prompt_ref": "woman in a green coat"}
      ],
      "duration": 5,
      "width": 864,
      "height": 480,
      "fps": 24,
      "seed": 42,
      "acceptance": ["主要人物和门廊清楚可见，无明显画面崩坏"]
    }
  ]
}
```

旧包仍可使用 `reference_images`：第一张映射为 `first_frame`，其余为 `review_only`。提供 `asset_bindings` 时以绑定表为准，不要再维护一份互相矛盾的 `reference_images`。新包直接在 `prompt` 中交付最终全文，`negative_prompt` 留空。旧包的正负提示词兼容方式见运行输入快照与 [agent 工作指南](agent-guide.md)。

## 提示词模块的交付规范

H3 官方 Base 指南建议依次组织为 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。图生模式还需要说明输入帧的对应关系。场景、动作与声音描述使用英语；对白保留原语言和标点，放在带语言标签的 `<d>` 中；说话者与镜头编号保持一致。以上是新提示词的交接规范，程序不会替旧提示词补写这些段落。[官方 Base 指南](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)

完整参考模式的官方六段依次是 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。其中对象标签表达复用内容，图片、视频、音频标签表达对应输入，各类型分别编号。该规范仅说明未来交接方式；本分支没有因此增加 Ref2VA 工作流，不能提交一个六段提示词就声称参考音视频已进入模型。[官方 Ref 指南](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)

交接后，控制和审核不能改写有效提示词、删对白、追加修复建议或用参数覆盖 `prompt/seed/尺寸/资产绑定`。审核结果只决定保留或按预算换种子再生成；需要内容变更时由外部提示词模块形成新的输入包。

## 预算、结果与交付

每个镜头每个制作轮最多生成 3 次，包含首次。普通 `resume` 与 `retry` 不清零本轮预算；只有操作者明确发出 `retry --new-round` 才建立额外制作轮，历史 `attempts` 继续累计。不要让 agent 自动发出新轮命令来绕过上限。

画面审核通过后保留该 take。反复未通过而预算耗尽时，从已经完成且可解码的候选中随机保留一个，记录原因和候选，不把降级标成通过。审核不可用时同样从已有可用 take 中随机选择一个，并记录告警。镜头没有任何可用视频时报告缺失，独立镜头继续处理；不会用空视频冒充完成。

选中的片段先超分，再按计划顺序合成。ASR、剧本差异与字幕都不重新打开生成循环；人工终审接收成片及问题记录。

```text
run/
  input-package.json             原始输入记录
  package.json / config.json     运行输入与配置快照
  inputs/                       复制的剧本、资产与自定义工作流
  state.json / events.jsonl      状态、累计尝试与事件
  shots/<id>/attempt-001/        本次参数、请求、视频与画面审核
  film/version-001/
    draft.mp4 / manifest.json   成片与镜头来源
    audio/
      transcript.json          ASR 状态、片段与真实识别时间戳
      consistency.json         仅供参考的台词差异结论
      subtitles/
        subtitles.json         匹配及未对齐台词
        episode.srt            仅在存在可靠对齐时生成
  delivery/                    终审后的交付产物
```

交接报告应说明代码分支/提交、模型路径与实际版本、工作流配置、已完成镜头、降级镜头、缺失镜头、ASR 可用性、字幕遗漏及人工终审状态。模型路径和版本应来自实际部署记录，不能从文件名推断。无需增加签名、鉴权或另一套审计服务。
