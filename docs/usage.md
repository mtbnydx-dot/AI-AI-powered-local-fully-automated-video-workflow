<!--
<document version="0.2.0" updated_at="2026-10-28T21:51:26+08:00" keywords="CLI,输入包,状态,参数,复盘" />
-->

# 输入与运行记录

脚本包是一个目录，以 `package.json` 为入口。文件路径相对该清单解析；配置中 workflow 路径相对配置文件解析。

| 内容 | 字段 |
| --- | --- |
| 项目 | `title`、`script`（Markdown 文件）、`shots` |
| 镜头 | `id`、`prompt`、`negative_prompt`、`reference_images`、`duration`、`width`、`height`、`fps`、`seed` |
| 创作与调参 | `acceptance`（审核条件）、`parameters`（模型/workflow 参数覆盖） |

完整例子见 [package.json](../examples/product/package.json)。输入只在入口校验。原生 H3 使用 24fps、32 倍数画布，生成帧数对齐后由媒体处理裁到计划时长。剪辑统一成 `media.width/height/fps`，保留生成音轨，无音轨时补静音。

每次运行复制脚本和参考图，保存配置；变更原始脚本包不会影响进行中的运行。调整模型或 workflow 配置后新建运行。CLI 定点重做支持修改 Prompt 与反馈，已完成的其他镜头复用。

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
    video.mp4 / clip.mp4      原始输出与归一化片段
    review/                   抽帧、请求、回复、结构化评审
  film/review-001/            剪辑结果与整片评审
  delivery/version-001/      最终视频与超分产物
```

`max_attempts` 限制每镜头生成次数，`max_film_reviews` 限制整片修复循环。人工 `retry` 重置指定镜头和整片本轮预算，历史编号继续累加。Review 服务失败不消耗新的生成次数，续跑复用片段。进程强制退出时，尚未记录完成的任务可能需要重新执行；ComfyUI 超时任务 ID 会保留，应先确认服务器任务结果。

自动修复当前执行镜头重生成，使用 Review 的修改建议与新 seed。自由文本剪辑建议无法可靠转为编辑操作时，状态为 `needs_attention`，保留问题；调整脚本包的顺序或时长后新建运行。不会反复合并相同片段并声称问题已修复。

单个 CLI 进程管理一次运行，按配置并发调用独立 ComfyUI 实例。状态文件不提供多进程共同写入或跨节点调度语义。
