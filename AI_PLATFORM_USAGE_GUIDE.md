# 本地 AI 视频工作台：平台使用指南

面向：接手使用本平台的 AI 助手或操作人员。

本文只说明平台操作、接口和限制，不包含剧本、分镜或提示词创作指导。

文档更新：2026-10-23，后端版本 `2026.10.23.2`，前端任务式入口 `20261023-journey-1`。运行中的服务可能还是旧版本，应先核对。后端按静态文件内容生成缓存指纹，同一版本中修改 JS/CSS 也能更新浏览器缓存。

### 新版入口与状态

「图片创作」「视频生成」「视频处理」是三个独立入口；「作品」浏览当前输出目录；「任务」统一查看图片和视频任务；「下载中心」只管理模型资源。首次安装仍从「环境与模型」开始。日常启动恢复上次的入口，首次默认进入视频生成表单，不自动提交任务。

视频各操作保存独立参数，提示词和项目标签也会在同一浏览器内保存。后期开始前核对顶部的「当前视频素材」，可以从作品选择，或上传本地视频。从作品挑选后返回原来的处理工具。超分目标按实际视频尺寸计算；没有实际尺寸时会标为估算。图片完整历史仍在图片创作页。浏览器草稿不是工程文件备份，清除网站数据会丢失草稿。

「新镜头」只清除当前素材与后期关联、上传选择，不删除作品，也不取消已提交任务。新镜头不会自动使用上个镜头的插帧或超分结果。刷新页面会恢复当前镜头关联；浏览器文件上传必须重新选择。

自动后期使用提交时保存的参数和步骤，后续修改不会改变已排队任务。同一任务的重复完成通知不会重复发起后期。批量任务或多个镜头并行时，只自动衔接当前镜头；其他结果保留在作品中，需手动选择后继续。关闭页面后不承诺继续自动衔接；任务本身仍由服务端执行，返回页面后先检查任务与作品，避免重复提交。

任务列表支持 `GET /api/jobs?status=active|success|failed|video|all&project=...&limit=20&offset=0`，先筛选再分页。只读预览使用 `GET /api/jobs/{id}?refresh=false`，不会刷新推理生命周期或释放显存。默认 `refresh=true` 的旧接口仍保留原行为。

本轮仅验证静态图、CPU 逻辑与模拟 API 的桌面/手机交互，未调用 GPU，未启动 ComfyUI。

## 1. 工程与服务入口

本文件所在目录是工程根目录。模型、ComfyUI 本体、前端 Python 环境和输出目录可能分开放置，不要自行迁移或重新安装。

| 项目 | 入口 |
| --- | --- |
| Windows 统一启动器 | 工程根目录 `START_WORKFLOW.bat` |
| macOS 启动器 | 工程根目录 `START_WORKFLOW.command` |
| 前端默认地址 | `http://127.0.0.1:7860` |
| ComfyUI 默认地址 | `http://127.0.0.1:8188` |
| 运行版本 | 前端 `GET /api/health` 返回的 `app_version` |
| 接口结构 | 运行中前端的 `/openapi.json` |
| 详细配置说明 | [README.md](README.md)、[小白指南](BEGINNER_FRONTEND_GUIDE.md) |

地址以当前配置为准。不要仅凭源码已更新就判断运行进程已更新；也不要仅凭文件已下载就判断 ComfyUI 已加载模型或节点。

外部 AI 若没有本机文件或网络工具，只能提供操作说明，不能直接控制本平台。远程 AI 自己的 `127.0.0.1` 不是用户的电脑；应使用已配置的客户端 / 服务端模式，不要直接把 ComfyUI 暴露到公网。

## 2. 操作边界

- 先确认本次任务是否允许使用 GPU。已有“不调用 GPU”的限制时，不得自行生成、启动 ComfyUI、加载或卸载模型。
- 启停其他服务、卸载 vLLM、更新 ComfyUI/PyTorch、删除文件和付费云端调用，需要用户明确授权。
- 不删除历史输出、不覆盖旧工作流、不重置项目、不清空其他人的队列。
- 启动器可能创建前端虚拟环境并安装依赖，不是纯只读检查。
- 无 GPU 授权时，可读源码以及已运行服务的 `/api/health`、`/api/jobs`、ComfyUI `/queue`、`/object_info` 元数据。不要主动进入会触发硬件探测的环境页。
- `GET /api/workflow` 会查询运行时并做硬件相关校验，不应当作无 GPU 阶段的纯静态检查。
- 队列为空不等于显卡空闲；其他服务可能占用显卡。不得自行关闭它们。

## 3. 前端操作顺序

### 环境与安装

1. 已获相关授权时启动统一入口，打开前端。
2. 在「环境与安装」确认系统、硬件、实际 ComfyUI 路径、Python 环境、连接状态和缺失项。
3. 在模型控制台选择目标模型，再查看该模型对应的安装计划。
4. 需要安装时，使用平台安装按钮或下载中心；不要一次下载所有模型。
5. 新节点安装后通常需要重启 ComfyUI 才会加载。先确认没有任务和资源冲突，再按授权操作。
6. 安装完成后重新检查配置。节点目录存在、权重存在、节点已注册、真实生成成功是四种不同状态。

前端可以在 ComfyUI 未启动时独立运行。静态 HTML/JS/CSS 更新只需刷新网页；Python 后端更新需要重启前端服务。安装新的 ComfyUI 节点需要重新加载 ComfyUI，刷新浏览器不能代替后者。

### 视频工作流

不再强制按六步顺序。生成新视频走「视频生成」，处理已有视频走「视频处理」。

| 步骤 | 操作 |
| --- | --- |
| 关键帧输入 | 在视频生成表单上传，或从图片模块送入；直接进入生成表单，是否必填取决于当前模型 |
| 视频生成 | 选择模型与变体，填写用户提供的提示词，上传所需素材，设置尺寸 / 帧数 / 参数，检查配置后提交 |
| 视频微调 | 使用上一步结果或上传视频，按需去闪、调亮度 / 对比度 / 饱和度 / 锐度 |
| 插帧 | 使用 RIFE 等已安装路线；检查输出帧率，不要把插帧当作内容修复 |
| 清晰度增强 | 选择已安装的超分模型与倍率，核对目标尺寸 |
| 成片拼接 | 添加视频、排序，统一交付设置并合成 |

生成表单默认显示「常用参数」，需要 H3 LightX/Attention、LTX 采样链路、步数等设置时切换「全部参数」。切换显示级别不重置已经设置的值。

完成后可以直接使用文件，或在结果区的「继续处理」选择修复、插帧、超分、拼接。该操作明确使用所点结果作为新输入，允许超分后再去闪等顺序，不会退回旧原片。生成页的自动后期是另一项显式选项；手动处理不会继承其勾选。涉及 GPU 的后处理同样受使用授权限制。

### 文生图模块

顶部「图片创作」是独立工作台。选择模型后，按其要求设置参数和参考图片，检查配置后生成。模型选择会同步相应能力及缺失项；不能把一个模型的编码器或 LoRA 套到另一种架构。

本文不列固定的全部模型数量；以当前页面和模型目录为准。默认不启用成人内容模式，使用参考人物素材需有授权。

## 4. 模型与输入对应关系

| 路线 | 输入 | 平台限制 |
| --- | --- | --- |
| H3 FL2VA T2V | 文本 | 本地 H3 INT8 路线按 CUDA 校验 |
| H3 FL2VA I2V | 文本 + 首帧；可加尾帧 | 首帧必填，不要只传尾帧 |
| H3 Ref2VA | 文本 + 参考图片 / 视频 / 音频 | 独立扩散权重；至少一个参考素材，最多 9 张图、3 段视频、3 条音频 |
| LTX 2.5 T2V / I2V | 按变体使用文本、首帧或首尾帧 | INT8 / NVFP4 为不同安装档；参数不要照抄 H3 |
| Wan、Hunyuan、Mac 路线 | 以对应模型显示为准 | 根据硬件与已加载节点选择，不把 Mac 统一内存等同 CUDA 显存 |
| 标为 fal 云端的路线 | 按云端表单要求 | 不属于本地生成；可能计费并上传素材，需明确授权 |

H3 Ref2VA 的提示词标签为 `<Picture 1>`、`<Video 1>`、`<Audio 1>`，各类型独立编号。具体文本由用户或负责内容的 AI 提供。

LTX 2.5 前端档位：

- `preview`：快速试镜，单阶段 + Conv VAE。
- `balanced`：均衡成片，两阶段 + Conv VAE。
- `quality`：质量档，两阶段 + DiffVAE。
- `custom`：手动两阶段 / 解码器设置才直接决定链路。

LTX 2.5 首尾帧路线会使用单阶段配置。需要固定时长时关闭 `ltx25_auto_duration`，避免自动时长头改变输出长度。

## 5. 尺寸、时长与参数

- `width` / `height` 是模型生成画布，不一定是最终发布尺寸。平台通常按 32 像素对齐；以预检返回值为准。
- `length` 是帧数，不是秒数。时长约等于 `length / fps`。
- H3 固定 24 FPS，帧数按 `17n+5` 对齐：73 帧约 3.04 秒，124 帧约 5.17 秒。当前后端上限为 362 帧，约 15.08 秒。
- LTX 2.5 使用 `8n+1` 帧规则，例如 121 帧在 24 FPS 下约 5.04 秒。
- 标准横屏 1080P 在 H3 中使用 `1920x1088` 内部画布，输出裁到 `1920x1080`；竖屏为 `1088x1920` 裁到 `1080x1920`。
- `seed=-1` 为随机；记录具体 seed 便于对照，但不保证跨模型或跨参数复现相同画面。
- `steps` 是采样步数。LightX 档会锁定其训练配方，不能只改主界面的步数。
- 高级参数通过 `advanced` JSON 字符串提交；字段和默认值从 `/api/video-params` 获取。
- 每次切换模型后，重新确认输入要求、尺寸、帧数和高级参数；不要沿用不属于新模型的隐藏参数。

## 6. H3 新功能

选择本地 H3 后，视频生成页显示「H3 创作」面板；加速与 Attention 在高级参数中。

| 功能 | 参数 / 操作 | 注意事项 |
| --- | --- | --- |
| 原生质量基准 | 「恢复原生质量基准」 | 20 步、res_multistep、shift 12/3、PyTorch Attention；FL2VA 用 simple，Ref2VA 用 beta；关闭创作扩展和加速 |
| 新版 LightX 4 步 | `h3_acceleration_profile=lightx4_v12_768p` | FL2VA 专用；4 步、Euler/simple、shift 6/3 |
| LightX 768P 8 步 | `h3_acceleration_profile=lightx8_v10_768p` | 8 步，与旧 544P 版本是不同文件，不得静默替换 |
| INT8 Attention | `h3_attention_backend=kitchen_int8` | 仅修改 H3 模型的注意力后端；需注册 `comfy kitchen attention`，不可用时拦截 |
| 动作 / 结构控制 | `h3_task=control`，上传控制视频 | 默认 Canny 自动提取边缘；pose/depth/hed/mlsd 要求上传已经预处理的控制视频 |
| 局部重绘 | `h3_task=inpaint`，上传原视频与遮罩 | 白色重绘、黑色保留；默认保留原音轨，无声原片保持无声 |
| 潜空间超分 | `h3_latent_upscale=true` | 生成中的两段式流程，不是现成 MP4 后处理；显示起始、精修和最终输出尺寸 |

控制 / 重绘要求：

1. 只开放 FL2VA，不支持 Ref2VA。选择 I2V 时仍需首帧。
2. 控制素材、原视频与遮罩视频必须覆盖实际对齐后的生成时长；静态遮罩图片不要求时长。
3. 原视频和遮罩必须同宽高。静态图片遮罩会复用于所有帧，移动区域应提供逐帧遮罩视频。
4. `h3_control_strength` 默认 0.7；`h3_control_start/end` 默认 0 / 0.8，表示采样过程比例，不是视频时间；开始必须小于结束。
5. `h3_preserve_source_audio=true` 保留原片音轨；关闭后跟随 H3 原生音频设置。

潜空间超分默认：`h3_upscale_factor=1.5`、`h3_refine_steps=8`、`h3_refine_denoise=0.35`，保留第一段音轨。它不保证降低峰值显存，也不保证画质必然优于原生生成。

**组合限制：**控制、重绘和潜空间超分要求 `h3_acceleration_profile=native`，不能叠加 LightX / Spectrum；控制 / 重绘不能同时启用潜空间超分。现成视频的增强应使用「清晰度增强」步骤。

新增创作权重和节点使用独立安装包 `minimax-h3-creative-addon`。前端「安装创作扩展」不会替换 H3 主模型，也不会自动启动 ComfyUI；现有 VideoHelperSuite 会保留。

验证边界：上述新增 H3 功能完成了 CPU 图结构、接口和前端回归，尚未因此获得 GPU 出片验证。首次运行仍需实际检查文件、节点、显存条件和输出。

## 7. API 使用

优先调用本平台前端 API，保留平台的模型解析、预检、任务记录和输出管理。不要绕过它直接 POST ComfyUI `/prompt`，除非用户明确要求或正在排查特定问题。

| 接口 | 作用 |
| --- | --- |
| `GET /api/health` | 查看前端运行版本 |
| `GET /api/video-params` | 获取各 mode 的参数 schema 与能力 |
| `GET /api/workflow` | 创建并预检工作流；`include_graph=true` 可返回图 |
| `POST /api/generate` | 提交一次生成，使用表单；带文件时使用 multipart |
| `GET /api/jobs?limit=20` | 列出任务，返回字段为 `items` 和 `count` |
| `GET /api/jobs/{job_id}` | 刷新单个任务；可能触发平台配置的收尾操作，只在已授权执行阶段使用 |
| `POST /api/jobs/{job_id}/cancel` | 取消指定任务，属于写操作；仅用于用户授权处理的任务 |
| ComfyUI `GET /queue` | 核对运行中与等待中的任务 |
| ComfyUI `GET /history/{prompt_id}` | 核对对应执行历史 |

### 模式 ID

`mode`、`model_profile` 和安装包 ID 不同，不可互换。

| 用途 | `mode` | `model_profile` |
| --- | --- | --- |
| H3 T2V | `minimax_h3_t2v` | `minimax_h3_fl2va_int8_t2v` |
| H3 I2V | `minimax_h3_i2v` | `minimax_h3_fl2va_int8_i2v` |
| H3 Ref2VA | `minimax_h3_r2v` | `minimax_h3_ref2va_int8_r2v` |
| LTX 2.5 INT8 T2V | `ltx25_t2v` | `ltx25_distilled_int8_t2v` |
| LTX 2.5 INT8 I2V | `ltx25_i2v` | `ltx25_distilled_int8_i2v` |

### 表单字段

| 类型 | 字段 |
| --- | --- |
| 常规 | `mode`、`model_profile`、`prompt`、`width`、`height`、`length`、`fps`、`seed`、`steps`、`cfg`、`project` |
| 高级参数 | `advanced`，值为 JSON 字符串，不是直接 POST 的 JSON 对象 |
| 首尾帧 | `image`、`end_image` 文件字段 |
| Ref2VA | 可重复的 `reference_media` 文件字段 |
| H3 控制 | `h3_control_video` 文件字段 |
| H3 重绘 | `h3_source_video`、`h3_mask` 文件字段；遮罩可为图片或视频 |

`GET /api/workflow` 预检使用相同的模式、尺寸和参数，并按实际素材同步以下布尔字段：

```text
has_image
has_end_image
has_reference_media
has_h3_control_video
has_h3_source_video
has_h3_mask
h3_mask_is_video
```

预检仅知道素材是否存在，不能代替上传后的实际时长 / 尺寸校验。预检失败时不得继续提交生成。

H3 原生档 `advanced` 的结构示例；仅展示参数，不会执行请求：

```json
{
  "h3_acceleration_profile": "native",
  "h3_attention_backend": "pytorch",
  "h3_task": "generate",
  "h3_latent_upscale": false,
  "sampler_name": "res_multistep",
  "scheduler": "simple",
  "h3_shift_video": 12.0,
  "h3_shift_audio": 3.0,
  "denoise": 1.0,
  "h3_audio": true
}
```

此示例的 `scheduler=simple` 对应 FL2VA；Ref2VA 使用其 schema 默认的 `beta`。实际提示词和素材由用户提供。

## 8. 任务跟踪与结果保存

1. 提交前先核对现有任务，不要让同一素材因重试被重复排队。
2. 每次 POST 成功后立即记录返回的 `id`（平台 job ID）与 `prompt_id`（ComfyUI ID）。
3. 保存完整请求参数、源素材、提交时间和输出记录；两个 ID 不能混用。
4. 通常每 5-10 秒查询一次任务，避免高频轮询。单卡默认串行处理。
5. `completed=true` 不等于成功，也可能是失败或取消；还要检查 `status`、错误信息和 `media`。
6. 使用返回的媒体 URL 或 `filename/subfolder/type` 获取结果，不猜测输出绝对路径。
7. 确认实际视频文件可完整解码，宽高、帧率、时长和音轨符合请求，再标记完成。
8. 中文重命名放在交付副本上，保留平台原始输出与索引。不要把模型、视频、参考图片或运行配置同步进 GitHub 发布目录。

HTTP 超时或客户端退出后，先用 `/api/jobs`、队列与执行历史寻找原任务。不要直接重新 POST，也不要通过清空队列解决状态不一致。

## 9. 常见问题

| 现象 | 检查顺序 |
| --- | --- |
| 没有 H3 创作面板 | 比较源码版本与 `/api/health`；检查旧前端是否仍在运行；按授权重启前端后刷新页面 |
| 权重下载完成但仍提示缺失 | 检查实际模型目录、下载是否完整、模型下拉中的真实名称；不要重复下载到另一目录 |
| `MinimaxH3LatentUpscaler3D` 缺失 | 确认节点目录存在，再检查是否重启过 ComfyUI、启动日志是否报导入错误 |
| 模型名带反斜杠或路径不匹配 | 以 `/object_info` 返回的名称为准，使用平台解析，不能手工沿用另一操作系统的路径 |
| 节点缺少必填输入 | 对照当前 `/object_info` / `/openapi.json`，不要继续反复提交旧图 |
| `SaveVideo` 编码器参数错误 | 新版可能使用 `format.codec`；本平台按连接到的节点 schema 兼容转换，不要固定沿用旧字段 |
| INT8 Attention 不可用 | 检查注意力后端枚举；回到原生或 PyTorch，不做全局替换 |
| 控制素材过短 / 遮罩错位 | 核对 H3 对齐后的帧数及原片 / 遮罩宽高 |
| 显存不足 | 停止追加任务，保留失败日志；按授权调整尺寸 / 帧数或换档位，不能自行卸载其他模型 |
| 队列完成但前端没显示结果 | 按平台 ID 和 ComfyUI ID 核对历史、媒体路径及任务状态，不直接重生成 |
| 去闪 / 超分后仍有严重形变 | 后处理不保证修复内容结构；保留原片和处理版，把问题交给内容负责人判断 |

## 10. 离线检查与源码入口

以下测试用于代码 / 模拟接口验证，不证明真实 GPU 生成成功。使用已有项目虚拟环境，不为测试升级依赖。

```powershell
$env:BEGINNER_FRONTEND_DISABLE_GPU_PROBES = "1"
$env:BEGINNER_FRONTEND_DISABLE_JOB_PERSISTENCE = "1"
$env:CUDA_VISIBLE_DEVICES = "-1"
.\.venv\Scripts\python.exe -B scripts/test_h3_creative.py
.\.venv\Scripts\python.exe -B scripts/test_platform_upgrade.py
```

| 文件 | 内容 |
| --- | --- |
| `beginner_frontend/app.py` | API、工作流构建、运行时预检、任务与输出管理 |
| `beginner_frontend/video_params.py` | 模型参数、默认值、选项与能力 |
| `beginner_frontend/h3_workflows.py` | H3 控制、重绘、Attention 和潜空间超分图扩展 |
| `beginner_frontend/model_catalog.py` | 模型资产、安装包与节点来源 |
| `scripts/install_workflow_assets.py` | 下载与节点安装 |
| `scripts/self_check.py` | 整项目离线回归与发布目录检查 |

接手时以实际代码、运行服务和任务记录为准。不要把历史检查结果当作当前状态，不要把“配置完成”写成“已经出片”。

created by zlk 2026
