# 使用与运行记录

## 安装与输入

从仓库根目录安装；三个 CLI 共用应用环境。FFmpeg 由 `imageio-ffmpeg` 提供。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate
# GPU 成片转写需要额外安装：
python -m pip install -e '.[asr]'
```

`scriptpipe` 默认使用 Ark `deepseek-v4.1-flash`、环境变量或 `.env` 中的 `DEEPSEEK-KEY`，endpoint 为 `https://ark.cn-beijing.volces.com/api/coding/v3`。请求显式开启 thinking；规划、写作、修复、审阅均继承 `--reasoning-effort high`。`--model glm-5.3` 自动选择 `GLMKEY`，也可通过 `--key-name` 指定。`--max-tokens` 默认 65536，包含思考与最终输出的预算。`imagepipe` 使用 [配置](../configs/imagepipe.yaml) 的服务地址、`env_file` 和 `key_name`，默认键名 `QWEN-IMAGE`。本地视频服务准备见 [部署](deployment.md)。

## 顺序运行

```bash
# 长剧：原文 → 整季剧本和图片槽位；短剧去掉 --long-form
scriptpipe --source /data/project/source.txt --notes /data/project/editor_notes.md \
  --long-form --episodes 8 --episode-minutes 5 --output /data/runs/script \
  --delivery /data/season --production-config configs/b200.yaml

# 补图：输入为含 image_slots.json 的整季或单集目录
imagepipe run /data/season --config configs/imagepipe.yaml --output /data/runs/images
imagepipe status /data/runs/images
imagepipe resume /data/runs/images

# 先验证一集，再运行整季；每集读取自身 production.yaml
videopipe validate /data/season/episodes/ep01/package.json \
  --config /data/season/episodes/ep01/production.yaml
videopipe run-season /data/season --output /data/runs/video-season

# 从已有记录重新收集交付
videopipe collect /data/runs/video-season
```

`--notes` 可省略。短剧交付到 `<output>/delivery/`；长剧可以指定 `--delivery`，也可用 `--stop-after season|plans|drafts|review` 在阶段结束时暂停。剧本续跑使用相同命令和输出目录，复用已存阶段；调整素材、创作 brief 或模板后用新输出目录开始，避免复用旧稿。

图片的 `run` 遇到已有 `tasks.json` 会读取已存计划；目标图片存在即复用。全剧包内的状态字段保留文字导出时的快照；分批交付包在图片齐备后写入实际交付状态。视频入口按实际文件是否存在校验。

## 全剧文本、分批补图与交付

集数由 `--episodes` 决定，长剧每集目标时长由 `--episode-minutes` 决定。保留每场 12 镜、每镜 5.875 秒的既有流程，目标时长取最接近的整场数：5 分钟对应 4 场、48 镜、4 分 42 秒。以下 64 是调用参数示例，可按改编密度调整。

```bash
EPISODES=64
scriptpipe --source /data/project/source.txt --notes /data/project/editor_notes.md \
  --long-form --episodes "$EPISODES" --episode-minutes 5 \
  --reasoning-effort high --review-reasoning-effort high \
  --output runs/scriptpipe/my_drama --delivery projects/my_drama \
  --production-config configs/b200.yaml

imagepipe run projects/my_drama --episodes ep01-ep10 \
  --config configs/imagepipe.yaml --output runs/imagepipe/my_drama/ep01-ep10 \
  --delivery deliveries/剧本my_drama_ep01-ep10

# 后续批次沿用同一个全剧目录及已经生成的参考图
imagepipe run projects/my_drama --episodes ep11-ep20 \
  --config configs/imagepipe.yaml --output runs/imagepipe/my_drama/ep11-ep20 \
  --delivery deliveries/剧本my_drama_ep11-ep20

videopipe run-season deliveries/剧本my_drama_ep01-ep10 --output runs/video/my_drama/ep01-ep10
```

`--episodes` 同时接受 `1-10`。只为选中集生成首帧及其所需的共享参考、场景锚点和依赖图；同一全剧目录中的人物、服装、场景和道具图直接复用。补图完成后，`--delivery` 导出仅含选中集的索引、剧本、配置和实际图片，保持原有 ID、prompt、seed 和图片内容。全剧 `season.json` / `season_bible.md` 随包保留，供后续衔接。

`projects/my_drama` 保存全剧文本和长期复用的资产；`runs/scriptpipe`、`runs/imagepipe` 保存阶段记录；`deliveries/剧本my_drama_ep01-ep10` 是交给视频端的独立目录。`imagepipe resume runs/imagepipe/my_drama/ep01-ep10` 会续跑补图并完成同一批次交付。调整补图范围时使用对应的新运行目录。

对于仍在连载的原文，可使用现有 `--notes` 明确当前素材的阶段终点、保留的悬念和改编取舍，使全剧规划沿用素材的实际范围。

## 单集、返工与批准

```bash
videopipe run /data/season/episodes/ep01/package.json \
  --config /data/season/episodes/ep01/production.yaml --output /data/runs/episode
videopipe status /data/runs/episode
videopipe resume /data/runs/episode
videopipe retry /data/runs/episode --shot ep01_s003 --feedback "记录返工原因"
# 本轮预算耗尽后，明确开始新一轮：
videopipe retry /data/runs/episode --shot ep01_s003 --new-round
# 仅 human_approval: true 的运行会进入 awaiting_approval：
videopipe approve /data/runs/episode
```

`run` 要求新的输出目录。默认自动交付；`--auto-approve` 可覆盖人工批准配置。`run-season --config CONFIG` 为新建的各集运行统一选配置，已有运行继续使用自身快照。整季按索引顺序执行，未完成时返回非零退出码；每集内部仍并发生成。

## 交接文件

```text
season/
  season.json / season_bible.md     整季结构数据与可读设定
  screenplay.md / quality.json      全季剧本与文字检查结果
  overview.json                     集顺序、package 路径、数量和计划时长
  image_slots.json                  shared_assets + first_frames
  assets/review/                    共享人物、服装、地点与道具图
  episodes/<episode_id>/
    script.md / storyboard.json     单集可读剧本与结构化镜头
    package.json / production.yaml 视频输入与配置
    image_slots.json                slots；共享引用带 source_path
    assets/                         实际首帧与集内审核参考
```

| 输入字段 | 含义 |
| --- | --- |
| `package.title / script / shots` | 标题、剧本文件、按观看顺序排列的镜头；路径相对 `package.json` |
| `shots[].id / prompt` | 稳定镜头 ID、完整有效 H3 prompt；对白使用 `<d>[Chinese]台词</d>` |
| `duration / width / height / fps / seed` | 计划时长与生成规格；内置 H3 要求 24fps、宽高为 32 的倍数 |
| `asset_bindings[]` | `asset_id`、`path`、`role`，可选 `prompt_ref`；所有路径需要真实文件 |
| `role: first_frame` | 内置 I2VA 的唯一首帧；没有首帧时选择 T2VA |
| `role: review_only` | 审核参考；人物、服装、地点和道具先由 imagepipe 合进首帧 |
| `acceptance / parameters` | 可观察的审核要求、推理参数覆盖；文本、seed、尺寸、素材绑定使用专门字段 |
| `image_slots` 中的 `asset_id / path / prompt_en / reference_asset_ids` | 图片任务 ID、写入位置、英文描述、有序引用依赖；`source_path` 表示共享图复制来源 |

旧包的 `reference_images` 仍可读取：第一张映射为首帧，其余为审核参考。旧 `negative_prompt` 在创建视频快照时合入有效 prompt 一次。新包直接提供完整 prompt。自定义 workflow 路径相对配置文件解析。

视频 `shots[].width/height` 控制生成画布，`media.width/height` 控制统一输出基准，再乘 `upscale_factor`。当前剧本导出将两者设为 576×1024；B200 的 2 倍超分对应交付 1152×2048。图片配置默认 720×1280，作为输入参考时由视频 workflow 处理。

## 到哪里查看运行结果

| 阶段 | 文件 | 看什么 |
| --- | --- | --- |
| 剧本 | `run.json`、`season.json`、`drafts/`；长剧另有 `plans/`、`scenes/` | 当前阶段、缓存设定与镜头稿 |
| 剧本 | `api/`、`checks/`、`reviews/`、`quality.json` | 请求/响应/用量、结构错误、审核意见、最终待修项 |
| 图片 | `tasks.json`、`state.json`、`events.jsonl`、`report.json` | 冻结任务、job ID、引用失败、复用/生成/复制数量 |
| 图片 | `originals/` 与包内资产路径 | 服务原图、统一尺寸后的可用图片 |
| 单集视频 | `input-package.json`、`package.json`、`config.json`、`inputs/` | 原输入记录、有效 prompt 与配置、复制的素材和自定义 workflow |
| 单集视频 | `state.json`、`events.jsonl` | 镜头状态、预算、错误、当前成片版本与耗时 |
| 单集视频 | `shots/<id>/attempt-NNN/` | 参数、workflow、提交 ID、视频、审核请求/抽帧/结果、超分结果 |
| 单集视频 | `shots/<id>/selection.json` | 选中 take、随机候选与降级原因 |
| 单集视频 | `film/version-NNN/` | 草片、实测时间轴 `manifest.json`、`audio/` 中的转写/差异/字幕 |
| 单集视频 | `delivery/version-NNN/` | `final.mp4`、`episode.srt`、`production_report.json` |

整季输出的 `runs/<episode_id>/` 是单集工作目录；`delivery/<episode_id>/` 收集成片、SRT 和报告，`delivery/logs/` 保存精简日志，`summary.json` 汇总状态，`delivery.tar.gz` 打包本次交付文件。收集器可利用已保存的 ASR `worker-result.json` 补全字幕，不重新推理。

缺图查包内绑定路径；图片失败查 `state.json` 的任务错误；视频停在 `needs_attention` 查镜头状态与 attempt 记录；审核降级和字幕粗对齐查生产报告。具体分支见 [工作流](workflow.md)。
