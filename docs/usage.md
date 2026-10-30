<!--
<document version="0.3.0" updated_at="2026-10-30" keywords="CLI,输入包,状态,参数,复盘" />
-->

# 输入与运行记录

脚本包是一个目录，以 `package.json` 为入口。文件路径相对该清单解析；配置中 workflow 路径相对配置文件解析。

| 内容 | 字段 |
| --- | --- |
| 项目 | `title`、`script`（Markdown 文件）、`shots` |
| 镜头 | `id`、原文 `prompt`、`negative_prompt`、`asset_bindings`、`duration`、`width`、`height`、`fps`、`seed` |
| 素材绑定 | `asset_id`、`path`、`role`（`first_frame` 或 `review_only`）、可选 `prompt_ref` |
| 创作与调参 | `acceptance`（审核条件）、`parameters`（模型/workflow 参数覆盖） |

输入只在入口校验。`first_frame` 作为生成首帧，`review_only` 仅供画面审核；只有审核参考图时使用 T2V。旧 `reference_images` 格式继续兼容：第一张作为首帧，其余作为审核参考，不能把它们当成已经接入的多图生成条件。

原生 H3 使用 24fps、32 倍数画布；对齐后的实际片段先接受画面审核，再逐片超分。媒体处理保留实测时长与生成音轨，不按计划时长强制截断尾音；无音轨时补静音以统一拼接格式，并保留缺音轨的事实。超分后统一格式再拼接，最终编码不重复超分。

每次运行复制脚本和素材，保存配置与提示词摘要；变更外部文件不会影响进行中的运行。中枢逐字传入冻结的 `prompt`，不自动追加负面提示、审核建议或改写文本。自动重做只更换已记录的 seed。修改提示词、引用或 workflow 时应由上游更新输入并新建运行；人工反馈作为记录，不拼入提示词。

```text
run/
  package.json / config.json   输入与配置快照
  inputs/                     剧本、参考图、自定义 workflow
  state.json                  状态、镜头尝试数、产物位置
  events.jsonl                带时间的步骤、耗时、评审、错误事件
  shots/<id>/attempt-001/
    shot.json                 本次镜头创作参数
    parameters.json           ComfyUI 实际推理参数
    workflow.json / prompt.json / history.json
    video.mp4                 原始生成输出
    review/                   抽帧、请求、回复、结构化评审
    upscale/                  本 take 的超分任务与输出（启用 ComfyUI 超分时）
  film/version-001/
    draft.mp4 / manifest.json  拼接成片、片段顺序与实际时间轴
    audio/                    ASR、剧本差异与字幕状态；ASR 默认关闭
  delivery/version-001/
    final.mp4 / production_report.json  最终视频与生产记录
```

`max_attempts` 限制每镜头当前制作轮的自动生成次数，默认及上限均为 3。普通 `resume` 和 `retry` 不清零当前预算；只有明确执行 `retry --new-round` 才开启新的人工制作轮，重置该轮 `budget_used`，累计 `attempts` 和历史产物继续保留。自动流程没有整片 Qwen 重审循环，也不会借此增加生成次数。

画面不通过且尚有预算时生成下一 take；预算耗尽或 Qwen 审核不可用时，从已有可用 take 中随机选一个进入超分与拼接，保存候选、选择结果和降级原因，不把降级写成审核通过。没有任何可用 take 时进入技术异常处理。ComfyUI 已保存的 `prompt.json` 用于恢复原服务器任务的查询和下载，超时不自动重复提交；未取得任务 ID 的提交不具备跨服务的恰好一次保证。

ASR 默认关闭。启用后在拼接成片上转写并生成时间戳，剧本差异仅供人工参考，不重生成视频。字幕仅使用能与真实时间轴对齐的上游台词；缺少台词、音轨或 ASR 结果时记录不可用状态。正常生产等待人工成片终审后进行最终编码；定位某镜头重做时复用其他镜头已就绪的产物。

单个 CLI 进程管理一次运行，按配置并发调用独立 ComfyUI 实例。状态文件不提供多进程共同写入或跨节点调度语义。
