# 生产输入指南：从脚本和资产到成片

适用分支：`kk-8xb200`。本指南说明内容怎么交给现有程序；模型安装和服务启动见 [部署说明](deployment.md)。当前分支完成了本地流程与接口测试，B200 上的模型共存、吞吐和画质仍需实际验证。

## 1. 你需要准备什么

每集准备一个脚本包，内容只有三类：

| 你提供的内容 | 放在哪里 | 用途 |
| --- | --- | --- |
| 完整剧本、镜头顺序、对白原文 | `script.md` | 给审核理解上下文，供 ASR 台词差异比较 |
| 每个镜头最终要生成的内容 | `package.json` 的 `shots[].prompt` | 直接进入 H3，决定画面和原生声音 |
| 已准备好的首帧图、角色或场景审核参考图 | `assets/`，并在 `asset_bindings` 里点名 | 根据绑定用途进入生成或审核 |

**程序不会把小说或 `script.md` 自动拆成镜头，也不会把只写在剧本里的对白自动补进生成提示词。** 每个镜头必须有自己的最终 `prompt`。如果希望生成对白并自动制作字幕，同一句台词应同时出现在剧本和对应镜头的 `<d>` 标签中。

你不需要填写 `take_id`、`prompt_hash`、任务 ID、审核结果或随机选片记录；这些由生产程序生成。JSON 不接受随意增加的字段，不要把这些运行记录塞回输入包。

建议一集一个包、一次运行一个新输出目录：

```text
/data/input/episode-001/
  package.json
  script.md
  production.yaml
  assets/
    porch-first.png
    woman-reference.png
```

所有示例路径都是节点上的示例位置，可以替换为你的实际目录。

## 2. 先写剧本，再确定每个镜头的最终提示词

以下内容保存为 `script.md`：

```markdown
# 门廊上的来信

写实电影风格，傍晚的安静庭院，绿色外套的女人等待一封来信。
使用直接切镜，不额外配乐。

1. s01，计划 5 秒：中景，女人站在门廊上拿着信封，望向院门。
   对白（中文）：我会在这里等你。
2. s02，计划 3 秒：切到院门旁的暖黄色灯，树叶在微风里轻动；无对白。
```

新提示词按 H3 Base 指南组织为三个部分：

- `integrated_multimodal_description`：人物、场景、动作、构图、镜头运动和有声内容。
- `overall_soundscape`：环境声音和动作声音。
- `non_diegetic_music`：背景配乐；没有配乐时填写 `N/A`。

对白示例：`The woman (S1) says: <d>[Chinese] 我会在这里等你。</d>`。不要翻译或改写引号里的台词；镜头之间保持人物特征和说话者编号一致。一个生产 `shot.id` 对应一个生成片段，示例里的 `[Shot 1]` 是该片段内部的 H3 镜头标签，不是要求所有片段共用同一个生产 ID。

有首帧图时，在提示词开头说明 `<Picture 1>` 对应开场；没有首帧时不要加入这条首帧指令。动作、声音和镜头要求应能在该片段时长内表达，避免一次塞入过多动作后期待中枢自动删改。

格式参考：[MiniMax H3 官方 Base 指南](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)。这是交接写法，程序不会因旧提示词没有三个段落而自动改写它。

## 3. 完整 `package.json` 示例

先把你自己的两张图片放到上述 `assets/` 目录，再保存以下内容。示例包含一个带首帧的 I2VA 镜头和一个没有参考输入的 T2VA 镜头：

```json
{
  "title": "门廊上的来信 / 第 001 集",
  "script": "script.md",
  "shots": [
    {
      "id": "s01",
      "prompt": "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\nintegrated_multimodal_description: [Shot 1] Live-action cinematic style. A medium shot shows the woman in the green coat from <Picture 1> standing on the porch and holding a sealed envelope. Preserve her appearance and the porch layout. The camera remains still as she looks toward the courtyard gate. The woman (S1) says in a calm voice: <d>[Chinese] 我会在这里等你。</d>\n\noverall_soundscape: Soft wind rustles the garden leaves.\n\nnon_diegetic_music: N/A",
      "asset_bindings": [
        {
          "asset_id": "porch-opening",
          "path": "assets/porch-first.png",
          "role": "first_frame",
          "prompt_ref": "<Picture 1>"
        },
        {
          "asset_id": "woman-reference",
          "path": "assets/woman-reference.png",
          "role": "review_only",
          "prompt_ref": "woman in the green coat"
        }
      ],
      "duration": 5,
      "width": 864,
      "height": 480,
      "fps": 24,
      "seed": 42001,
      "acceptance": [
        "主要人物穿绿色外套，站在门廊上并拿着信封。",
        "人物与首帧大体一致，没有明显画面崩坏。"
      ]
    },
    {
      "id": "s02",
      "prompt": "integrated_multimodal_description: [Shot 1] Live-action cinematic style. A close shot shows a warm yellow lamp beside a wooden courtyard gate at dusk. The camera remains still while nearby leaves move gently in the wind. No people are visible and there is no dialogue.\n\noverall_soundscape: A soft breeze rustles the leaves.\n\nnon_diegetic_music: N/A",
      "duration": 3,
      "width": 864,
      "height": 480,
      "fps": 24,
      "seed": 42002,
      "acceptance": [
        "画面主要内容为木制院门旁的暖黄色灯。"
      ]
    }
  ]
}
```

第二个镜头没有参考图，因此不能保证复现第一张图里的同一扇院门。如果必须保持同一场景的精确细节，应在输入阶段准备第二个镜头自己的首帧，并使用相同的 `first_frame` 绑定方式。

JSON 中长提示词仍是一个字符串，换行用 `\n`；不要加入注释或末尾多余逗号。

### 字段填写表

| 字段 | 填写规则 |
| --- | --- |
| `title` | 必填，非空项目或集名 |
| `script` | 必填，实际存在的剧本文件路径，相对 `package.json` 解析 |
| `shots` | 至少一个；数组顺序就是拼接顺序 |
| `id` | 镜头唯一标识，如 `s01`；以英文字母或数字开头，只含英文字母、数字、`-`、`_` |
| `prompt` | 必填，最终生成全文；不要填提示词文件路径，程序不会自动读取该文件 |
| `duration` | 计划秒数，正数，默认 3；不是最终成片的严格裁切长度 |
| `width` / `height` | 生成画布，默认 864 × 480；原生 H3 要求都是 32 的倍数 |
| `fps` | 原生 H3 填 24 |
| `seed` | 非负整数，默认 42；各次生成使用不同且有记录的 seed |
| `acceptance` | 可选，只列少量重要、可从画面判断的要求；不要把台词准确率、声线或配乐写成画面拒绝项 |
| `asset_bindings` | 可选；无生成首帧时走 T2VA，有首帧时走 I2VA |
| `negative_prompt` | 新包建议省略；如旧包使用该字段，输入模块会一次性以 `Avoid:` 合并，再冻结有效全文 |
| `parameters` | 一般省略；确需调整时可写受现有工作流支持的技术参数，例如 `{"steps":20}` |

`parameters` 不能覆盖 `prompt`、`seed`、尺寸、帧率、帧数、时长、反馈或参考资产。这些内容要填在正式字段中。任意新参数也不会自动接入 ComfyUI 图；当前图没有使用的参数不会产生预期效果。

生成帧数按 H3 要求对齐。按当前默认换算，请求 5 秒会使用 124 帧，24 fps 下约为 5.17 秒；拼接与字幕采用实际媒体时长，不裁掉尾音来凑整数秒。

## 4. 资产怎么绑定

每个绑定有 `asset_id`、`path`、`role`，以及可选的 `prompt_ref`：

| 用途 | `role` | 实际行为 |
| --- | --- | --- |
| 该镜头的开场画面 | `first_frame` | 最多一张，上传给 H3 并作为首帧 |
| 人物、服装、道具或场景的审核参考 | `review_only` | 只发送给画面审核模型，不作为生成条件 |

使用本地可读取的图片，建议 PNG 或 JPEG；路径相对 `package.json`，不要填写远程图片网址。资产 ID 在同一镜头内保持唯一。

**`prompt_ref` 是绑定说明，不是素材检索或文本替换命令。** 写上一个资产 ID、`<Picture 2>` 或“调用人物包”，并不会让模型自动加载其他文件。真正进入生成图的资产由 `first_frame` 指定；当前内置图没有多图角色拼装、尾帧、参考视频、参考音频或 Ref2VA 输入。

如果角色、服装和场景需要组合，应由上游资产制作模块先准备好该镜头首帧。控制中枢不会自行找图、拼图或替换资产。

旧包的 `reference_images` 仍可用：第 1 张是首帧，其余只供审核。新包优先用 `asset_bindings`，不要同时维护两份不同的资产清单。只有 `review_only` 而没有 `first_frame` 时仍然走 T2VA。

## 5. 选生产配置

从仓库根目录复制现有配置，然后根据本集需要修改副本：

```bash
cp configs/b200.yaml /data/input/episode-001/production.yaml
```

这份配置已经包含七个 ComfyUI 地址、Qwen 审核地址和以下生产规则：

- 生成及单片段超分使用 GPU 0–6；整张 GPU 7 供 Qwen 和可选 ASR 进程共享。
- 画面审核并发为 2，需与启动服务的 `REVIEW_MAX_SEQS` 对齐；增加到 4 前测实际显存和吞吐。
- `max_attempts: 3`，包含首次生成；`human_approval: true`，最后等待人工终审。
- 默认画布 864 × 480，2 倍超分，最终规格为 **1728 × 960**，不会自动变成 1920 × 1080。
- ASR 默认关闭。

`shots[].width/height` 控制生成尺寸，配置的 `media.width/height` 控制统一成片画布；通常保持二者一致。例如做竖屏时可同时改为 480 × 864，默认 2 倍输出就是 960 × 1728。仅修改镜头尺寸、没有同步 `media`，后处理仍会套用旧画布。

需要 ASR 和字幕时，在完整配置里替换 `asr` 段；不要把以下片段当成整份 B200 配置：

```yaml
asr:
  enabled: true
  model: /models/faster-whisper-large-v3
  gpu: 7
  compute_type: int8_float16
  language: zh
  timeout: 600
```

ASR 可选依赖和本地权重须按 [agent 工作指南](agent-guide.md#asr-和字幕)准备。关闭、识别错误或服务不可用都不会阻断成片；没有可用识别时间轴时不会生成伪造字幕。字幕从镜头 `<d>` 中取原文，再与 ASR 时间轴匹配；只在 `script.md` 写对白不足以生成这条字幕。

## 6. 校验、开始、查看与交付

以下命令在已经安装应用、且 ComfyUI / Qwen 服务已启动的节点执行。`python` 指应用所在 Python 环境，不是另一个未安装依赖的系统 Python。

先检查输入格式、文件路径和工作流约束；这一步不调用模型，也不验证 GPU 服务可用性：

```bash
python -m videopipe validate /data/input/episode-001/package.json \
  --config /data/input/episode-001/production.yaml
```

开始生产，输出目录必须是尚未创建的新目录：

```bash
python -m videopipe run /data/input/episode-001/package.json \
  --config /data/input/episode-001/production.yaml \
  --output /data/runs/episode-001-v1
```

查看状态：

```bash
python -m videopipe status /data/runs/episode-001-v1
```

中断后恢复原运行，不新建一轮，也不清零次数：

```bash
python -m videopipe resume /data/runs/episode-001-v1
```

状态到 `awaiting_approval` 时，观看 `film/version-001/draft.mp4`，查看降级镜头和音频报告。确认后执行：

```bash
python -m videopipe approve /data/runs/episode-001-v1
```

首次交付在 `delivery/version-001/final.mp4`，同时有 `production_report.json`；字幕匹配成功时还会复制 `episode.srt`。后续版本编号递增，以 `state.json` 中的路径为准。生产示例保留人工终审，不使用 `--auto-approve`。

## 7. 不通过时系统会做什么

| 情况 | 后续行为 |
| --- | --- |
| 明显画面不符，尚未生成满 3 次 | 保持提示词和资产，换 seed 生成下一 take |
| 第 3 次仍未通过 | 从当前制作轮已有可用 take 中均匀随机选一个，记录 `budget_exhausted` 后继续 |
| 画面审核不可用 | 随机选已有可用 take，记录 `review_unavailable` 后继续；不会为了审核故障再生成 |
| 只有一个可用 take | 使用这一个，并保留随机降级原因 |
| 没有可用 take | 标记该镜头缺失，其他镜头继续；该集尚不能完成 |
| ASR 台词差异、失败或关闭 | 记录结果，不触发重生成、不改变画面审核结果 |

需要对一个镜头再抽一次，并且本轮还有预算时，可以明确发起：

```bash
python -m videopipe retry /data/runs/episode-001-v1 \
  --shot s01 --feedback '人工要求该镜头再生成一次；保持原提示词'
```

`--feedback` 只写记录，不会变成新的生成指令。提示词、对白或资产需要变更时，回到外部输入包修改，再用新的输出目录发起运行；不要直接编辑运行目录里的冻结输入。外部文件变化也不会自动影响已开始的运行。

**只有你主动决定追加制作预算时**，才使用 `retry ... --shot s01 --new-round`。这会建立额外人工制作轮，累计历史继续保留；agent 不得把它当作自动重试手段。

## 8. 去哪里看结果和问题

| 文件或字段 | 看什么 |
| --- | --- |
| `state.json` 的 `shots.s01.attempts` / `budget_used` | 历史累计次数 / 当前制作轮已用次数 |
| `state.json` 的 `shots.s01.selection` | 选中的 `take_id`、`reason`，随机时还有 `candidates`、`selection_seed` 和 `flags` |
| `shots/s01/attempt-001/shot.json` | 该次实际提示词、seed 和资产 |
| `shots/s01/attempt-001/review/result.json` | `available`、`passed`、问题和警告；反复失败的每次结果都保留 |
| `film/version-001/manifest.json` | 所选片段、实际时长及其在成片中的起始时间 |
| `film/version-001/audio/transcript.json` | ASR 是否可用、识别文字和时间戳 |
| `film/version-001/audio/consistency.json` | 台词差异建议；不是通过门槛 |
| `film/version-001/audio/subtitles/subtitles.json` | 字幕匹配和未对齐记录 |
| `events.jsonl` | 生成、审核、选片、异常、耗时与人工操作 |
| `delivery/version-001/production_report.json` | 本次交付采用哪些镜头及其问题记录 |

日志里的“已交付”与“所有镜头审核通过”含义不同：随机降级片段会参与成片，但保留未通过或审核不可用的事实。

## 9. 交给内容 agent 的简短指令

可以将以下要求连同剧本和图片交给准备输入的 agent：

> 为 `kk-8xb200` 工作流准备生产输入包。交付 `package.json`、`script.md` 和实际引用的资产文件，镜头顺序放在 `shots` 数组。每个镜头提供最终 H3 提示词，保留原台词和标点，需生成及字幕化的台词放进 `<d>[语言]…</d>`。明确区分 `first_frame` 和 `review_only`，不把多图、音视频参考或 `prompt_ref` 当成已支持的自动素材调用。新包直接交付完整 `prompt`，不填写运行时字段。生成规格与 `media` 配置对齐。提交前用现有 `videopipe validate` 校验；不能自行启动额外制作轮、改写已冻结的输入或宣称已完成 B200 实测。

更详细的职责和开发约束见 [agent 工作指南](agent-guide.md)，完整交接约定见 [交接标准](handoff.md)。
