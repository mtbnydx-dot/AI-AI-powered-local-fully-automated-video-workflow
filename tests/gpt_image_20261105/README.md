# GPT Image 接入调研与独立实验

核实日期：2026-11-05（Asia/Singapore）。实验使用 `.env` 中用户指定的 `OPENAI`，直接请求 `https://api.openai.com`。改动和实验产物限定在 `tests/`。

## 1. 模型选择

质量优先的首选验证对象是 **`gpt-image-2.6-sunburst`**。官方把它定位为复杂细节、构图和精确编辑的选择；`gpt-image-2.6-flare` 适合之后比较效率。先固定 prompt、参考图、尺寸和质量设置，得到合格样本，再比较 Flare。这个选择依据官方定位；本实验没有进行跨模型质量排名。[官方提示词与选型指南](https://developers.openai.com/api/docs/guides/image-prompting)

当前模型目录和本账户 `GET /v1/models` 返回的图像模型如下：

| 模型 ID | 在本项目中的用途 |
| --- | --- |
| `gpt-image-2.6-sunburst` | 本次质量优先的主验证模型 |
| `gpt-image-2.6-flare` | 质量要求明确后的效率对照 |
| `gpt-image-2.5-sunburst` | 上一代质量型模型；有 `2026-09-08` 快照 |
| `gpt-image-2.5-flare` | 上一代速度型模型；有 `2026-09-08` 快照 |
| `gpt-image-2` | 旧链路或旧实验的对照；有 `2026-04-21` 快照 |
| `gpt-image-1.5` | 较早一代；保留兼容性比较价值 |
| `gpt-image-1-mini` | 成本敏感任务候选 |
| `chatgpt-image-latest` | 随 ChatGPT 图像版本更新的别名；生产追踪宜使用明确版本 |

官方还列有图像与 SVG、GIF 相关能力，本次聚焦视频链路使用的静态 PNG。[Sunburst 模型页](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst)、[图像生成 API](https://developers.openai.com/api/reference/resources/images/methods/generate)

`gpt-image-1` 已于 2026-10-23 关闭；DALL·E 2、DALL·E 3 已于 2026-05-12 关闭。[官方弃用记录](https://developers.openai.com/api/docs/deprecations)

## 2. 定价

以下是 Standard 请求的美元价格，单位均为 **每百万 token**。图像输入、文字输入、图像输出分别计费。

| 模型 | 文字输入 | 图像输入 | 图像输出 |
| --- | ---: | ---: | ---: |
| GPT Image 2.6 Sunburst / Flare | $5 | $8 | $30 |
| GPT Image 2.5 Sunburst / Flare | $5 | $8 | $30 |
| GPT Image 2 | $5 | $8 | $30 |
| GPT Image 1.5 | $5 | $8 | $32 |
| GPT Image 1 Mini | $2 | $2.50 | $8 |
| chatgpt-image-latest | $5 | $8 | $32 |

价格来源：[官方价格表](https://developers.openai.com/api/docs/pricing)、[1.5 模型页](https://developers.openai.com/api/docs/models/gpt-image-1.5)、[Mini 模型页](https://developers.openai.com/api/docs/models/gpt-image-1-mini)。当前 `chatgpt-image-latest` 价格与 1.5 一致，并不能据此推导它等于 2.6。

本次 Sunburst 单次费用估算：

```text
USD = (文字输入 token × 5 + 图像输入 token × 8 + 图像输出 token × 30) / 1,000,000
```

每张图片消耗的 token 随模型、尺寸和 quality 改变；相同单价意味着相同 token 成本。用响应 `usage` 计算每个任务的实际消耗，成本记录是估算，账单以平台为准。旧版 1.5 的 `1024×1536/high` 输出约 $0.20；这个数字仅适用于该旧模型、尺寸和质量组合。2.6 的本次实测费用记录在各任务的 `report.json`。[官方用量与费用说明](https://developers.openai.com/api/docs/guides/image-generation#cost-and-latency)

价格表为 2 / 2.5 提供 Batch 折扣；当前 2.6 模型页标记 Batch 为未支持。本次直接 Image API 调用按 Standard、未缓存输入估算。[2.6 模型能力](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst)

## 3. 请求格式和限制

| 项目 | Image API 规则 |
| --- | --- |
| 纯文本生图 | `POST /v1/images/generations`，JSON |
| 单图编辑、多图参考合成 | `POST /v1/images/edits`，本实验采用 multipart |
| prompt 数量 | 每次请求一个字符串；多个独立场景各自提交请求 |
| prompt 长度 | 最长 32,000 字符，按字符而非 token 描述 |
| 输入参考图 | 编辑请求 1–16 张，有序上传 |
| 输入文件 | PNG、JPEG、WebP，每张小于 50 MB |
| multipart 字段 | 多张图使用重复的 `image[]`，SDK 对应 `image=[...]` |
| 另一种编辑输入 | JSON `images` 数组，元素为 `image_url` 或 `file_id`；URL 可为 HTTPS 或 base64 data URL |
| JSON URL 限制 | `image_url` 字段最多 20,971,520 字符；与 multipart 文件大小是不同约束 |
| 输出张数 | `n=1..10`；各图共用这次请求的同一个 prompt，实验固定 `n=1` |
| 静态输出 | PNG、JPEG、WebP；本实验保存 PNG 的 `data[0].b64_json` |
| 局部 mask | 可选；PNG、<4 MB、匹配第一张参考图尺寸，透明区域指示编辑区 |

来源：[编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit)、[Python 文件上传与 mask 约束](https://developers.openai.com/api/reference/python/resources/images/methods/edit)、[生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate)。本次实测覆盖 0、1、5 张参考图，16 张上限来自官方协议。

2.6 支持 `quality=low/medium/high/xhigh/max/auto`；`complexity=low/medium/high` 控制构图规划，默认 high。本次 quality 固定 max，complexity 保持默认。编辑时省略 `input_fidelity`；它是旧模型的可调参数。尺寸由 API 的 `size` 设置，prompt 则描述画面。[2.6 参数说明](https://developers.openai.com/api/docs/guides/image-prompting#model-parameters)

自定义尺寸要求两边为 16 的倍数、长宽比例在 1:3 到 3:1。官方生成指南列出的像素范围为 655,360–8,294,400，最长边 3840；大于 2560×1440 的分辨率标为实验性。本次选 **1152×2048，9:16**，交付尺寸仍为 720×1280；二者同宽高比，可直接缩小。[尺寸说明](https://developers.openai.com/api/docs/guides/image-generation#size-and-quality-options)

纯文本请求示例：

```json
{
  "model": "gpt-image-2.6-sunburst",
  "prompt": "Create the canonical identity portrait ...",
  "size": "1152x2048",
  "quality": "max",
  "n": 1,
  "output_format": "png",
  "background": "opaque"
}
```

多图编辑保留同一组标量参数，用 multipart 依次上传 `image[]`。本实验的 `request.json` 记录实际字段和图片顺序，`response.json` 保留响应元数据、去掉图片 base64。

## 4. 提示词经验及其适用范围

**官方指南与本链路的直接对应：** 描述用途、画面、主体、可见细节；给每张参考图编号并指定用途；编辑时写清修改项与保留项。身份、服装、场景光线分别控制。用明确的构图和物理关系，例如手持物与桌面的间隔。当前 `imagepipe_character_identity`、`wardrobe_edit`、`first_frame` 模板已具备这些内容。本次先保留原模板，只有编译时的画布尺寸改为 1152×2048。[官方 Cookbook](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide)

**社区的一手经验：**

- [Reddit 的 GPT Image 2 身份一致性实验](https://www.reddit.com/r/Seedance_AI/comments/1w4yyk0/gpt_image_2_character_consistency_tutorial/)把场景图与身份图分开，并明确让身份参考只决定面部，光线由目标场景决定。适合转化为本项目的“identity 决定脸、wardrobe 决定衣服、scene 决定灯光与空间”约定。
- [OpenAI 开发者论坛的单角色实验](https://community.openai.com/t/collection-of-gpt-image-generator-2-0-issues-bugs-and-work-around-tips-check-first-post/1379535/99)建议明确单一角色、单一画面，避免模糊的 reference-sheet 表述诱导多视图拼版。本项目需要每个资产一张完整画面，现有模板方向一致。
- [社区 GPT Image 2.5 提示词库](https://github.com/VulcanEon/awesome-gpt-image-2.5-prompts)可用于查找构图案例和局部编辑写法；其部分提示词标注为视觉重建、未独立复现，适合提供候选思路。质量结论以本项目实际输出为依据。

以上社区例子主要针对 2 / 2.5。迁移到 2.6 时，采用可解释的提示结构，在目标模型验证。对本项目建议保留角色母图，把新的生成结果作为后续请求的参考；每次小修聚焦一个变化，继续写清身份、服装和空间中需要保持的部分。

可以使用的结构示意：

```text
Purpose: one opening frame for an image-to-video shot.
Scene: camera position, visible cast, lighting, physical starting state.
Image 1: the woman's facial identity and hairline.
Image 2: clothing for that same woman.
Image 3: the man's facial identity and hairline.
Image 4: clothing for that same man.
Image 5: architecture and fixed furniture.
Preserve: each identity, specified garment colors, fixed room geography.
Composition: each named person appears once, in one continuous camera view.
```

## 5. 正式接入需要改动的位置

以下是后续方案，当前生产实现仍使用 Qwen。

| 现有位置 | 当前行为 | GPT Image 接入方案 |
| --- | --- | --- |
| `imagepipe/client.py:41` | Qwen jobs / edits / references 三种提交接口 | text → generations；edit/reference → edits，按顺序上传 `image[]` |
| `imagepipe/client.py:46` | 传 steps、seed，尺寸取 32 倍数 | 使用 model、size、quality、n、output_format；省略 steps、seed；尺寸按 GPT Image 约束 |
| `imagepipe/pipeline.py:76` | 提交 job_id → 轮询 → 获取 URL → 下载 | await 生图响应 → 保存 base64 原图 → 交付；复用依赖等待、状态落盘和复制逻辑 |
| `imagepipe/models.py`、`configs/imagepipe.yaml` | Qwen 连接、90 秒请求、并发 16 / 活跃任务 64 | `api.openai.com` + `OPENAI`；增加 quality 与原生尺寸配置；按实际延迟和账户额度设超时、并发 |
| `imagepipe/plan.py:159` | 超过 10 张先裁减服装参考，最终检查 10 张上限 | 按 GPT Image 的 16 张预算选择参考；图片排序与 prompt 编号保持一致 |
| `imagepipe/pipeline.py:77` | 后续生成读取已缩至 720×1280 的 target | 质量优先时让后续编辑读取保存的原生母图；下游视频交付继续使用目标尺寸 |
| `imagepipe/delivery.py` | 中心裁切并缩放到目标尺寸 | 复用；9:16 原图对应 9:16 交付，保留原图用于高质量参考 |

直接 Image API 完成后记录 `x-request-id`、usage、模型、质量、参考图顺序、原图及交付图。请求 ID 用于问题追踪，恢复从已保存的本地结果继续；Qwen 的远程 job 轮询方式需随执行器调整。超时之后的生成是否已计费需要单独确认，实验脚本每次执行只发一次生成请求。

执行结构可以保持简单：

```text
compile_plan → 等待参考资产完成 → 带并发限制的 Image API 请求
             → 保存原图/usage → deliver_image → 已有 copy/export
```

本次复用了真实 `compile_plan` 生成 prompt 和真实 `deliver_image` 交付图片，使用仓库已有 httpx/Pillow，没有增加依赖。实验验证请求协议和三种典型任务；完整生产调度器的改造仍属于后续实施。

## 6. 复现与产物

从仓库根目录运行，输出目录应使用尚未存在的新目录：

```bash
.venv/bin/python tests/manual_gpt_image_api.py \
  --prompt-file tests/gpt_image_20261105/prompts/01_identity.txt \
  --output tests/gpt_image_20261105/repeat_identity

.venv/bin/python tests/manual_gpt_image_api.py \
  --prompt-file tests/gpt_image_20261105/prompts/02_wardrobe.txt \
  --reference tests/gpt_image_20261105/01_identity/original.png \
  --output tests/gpt_image_20261105/repeat_wardrobe
```

多参考图按 prompt 中的编号重复传入 `--reference PATH`。加 `--dry-run` 只生成请求记录。`--quality`、`--model`、`--size` 可用于之后对照；本轮不自动展开模型或质量参数组合。

- `manifest.json`：由现有计划编译得到的三个任务、输入来源与旧资产路径，仅用于追溯。
- `prompts/`：实际提交的英文提示词。
- 各任务目录的 `request.json`、`response.json`、`report.json`：请求、响应元数据、耗时与费用估算。
- 各任务目录的 `original.png`、`delivered.png`：原图和调用现有交付函数得到的图片。
- `RESULTS.md`：真实调用结果与图像检查结论，实验完成后写入。

PNG 被仓库现有 `.gitignore` 忽略，保存在本机实验目录。
