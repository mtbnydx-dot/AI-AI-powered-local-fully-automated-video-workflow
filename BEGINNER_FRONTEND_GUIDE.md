# Wan2.2 小白前端使用指南

## 启动

推荐用统一入口启动：

```powershell
E:\ai photo creat\video creat\START_WORKFLOW.ps1
```

macOS 迁移后用：

```bash
cd "/path/to/video creat"
chmod +x START_WORKFLOW.command
./START_WORKFLOW.command
```

统一入口会检查 ComfyUI 是否连得上，然后启动小白前端并打开 `http://127.0.0.1:7860`。如果 ComfyUI 还没启动，前端仍然会打开，第一步会提示缺什么。

旧入口仍可用：

```powershell
E:\ai photo creat\video creat\scripts\start_beginner_frontend.ps1
```

## 工作流顺序

前端已经按推荐流程改成 8 步向导：

1. `环境侦测`：自动检测系统、GPU/显存、ComfyUI、ffmpeg、节点和模型文件，并推荐每一步能用的模型。
2. `关键帧`：上传或确认首帧图片，固定人物、场景、构图。
3. `试镜头`：用 `TI2V-5B` 快速生成草稿，看动作和镜头运动。
4. `正式片段`：用 `I2V-A14B` 生成 720P 正片。
5. `闪烁修复`：用 ffmpeg `deflicker` 降低亮度闪烁、曝光跳动和轻微细节噪声。
6. `插帧后期`：用 `RIFE 2x` 给上一步视频插帧；也可以手动上传视频。
7. `清晰度增强`：用 `RealESRGAN_x2plus` 做 2x 视频超分，提高分辨率和边缘清晰度。
8. `拼接剪辑`：把多个短镜头导入剪映、达芬奇或 PR 拼成完整视频。

每一步完成后，右侧会出现 `继续下一步`。点击后会自动切到下一步，并保留上一阶段需要复用的信息。比如正片完成后，闪烁修复会自动使用正片视频；修复完成后，插帧步骤会自动使用修复后的视频；插帧完成后，清晰度增强步骤会自动使用插帧后的视频。

## 推荐顺序

1. 先看 `环境侦测`，确认这台机器适合跑哪一档模型。
2. 用生图模型先做关键帧，或直接上传外部关键帧。
3. 用 `TI2V 5B` 快速试动作。
4. 满意后用 `I2V A14B` 正式生成。
5. 有闪烁时先做 `闪烁修复`，再插帧和超分。
6. 多个 3 到 6 秒镜头导入剪映、达芬奇或其他剪辑软件拼接。

## 环境侦测和一键安装

第一步会显示：

- 当前系统：Windows / macOS、Python 版本。
- GPU 和显存：会区分单卡显存和多卡总显存。
- ComfyUI 状态：版本、队列、是否连上 `http://127.0.0.1:8000`。
- ffmpeg：闪烁修复需要 `deflicker` 和 `hqdn3d` 滤镜。
- 模型推荐：关键帧、TI2V 草稿、A14B 正片、T2V、闪烁修复、RIFE、超分、剪辑分别显示可用模型。
- 缺失项：模型、RIFE 权重、超分权重、自定义节点。
- 低配 / mac 路线：8GB-12GB CUDA、16GB-24GB、48GB、80GB+、Apple Silicon、CPU 会分别给建议。

如果有缺失项，点击 `一键安装缺失项`。它会下载或补齐：

- Wan2.2 TI2V-5B / I2V-A14B / T2V-A14B 相关权重。
- UMT5、Wan VAE、4 步 LoRA。
- `ComfyUI-VideoHelperSuite` 和 `ComfyUI-Frame-Interpolation`。
- `rife49.pth`、`RealESRGAN_x2plus.pth`、`4x-UltraSharp.pth`。
- 内置示例关键帧。

实际下载内容会跟随环境侦测的安装档位。Mac 低档不会下载 A14B；高内存 Mac 只会额外下载 Wan5B 所需文件；Windows/CUDA 主线才默认使用 `cuda-full`。

安装完成后需要重启 ComfyUI，让模型列表和自定义节点重新加载。

硬件判断规则：

- `96GB 单卡`：适合主跑 `Wan2.2 I2V-A14B 720P`，也是当前工作流的推荐档。
- `48GB 单卡`：更适合 `TI2V-5B 720P`、A14B 480P、短镜头或开启 offload；A14B 720P 可能 OOM。
- `双 48GB`：可以并行跑两个镜头或把部分任务分开跑，但默认不等于一张 96GB 单卡；A14B 720P 仍然主要看单卡显存。
- `macOS Apple Silicon`：会进入独立 Mac 视频路线。8GB/16GB 默认 LTX 低档，24GB-36GB 默认 LTX 均衡/质量档，48GB/64GB 可见 Wan2.2 TI2V-5B 480P 实验档，96GB/128GB+ 可见 Wan2.2 TI2V-5B 720P 高内存实验档。A14B 不作为 Mac 默认推荐。
- `Intel Mac / MPS 不可用`：只推荐关键帧管理、ffmpeg 后期、剪辑和低风险流程。

## macOS Apple Silicon 视频生成模式

Mac 不再只是打开前端和做后期。环境侦测会检查：

- macOS / Apple Silicon / Intel Mac。
- 芯片型号：M1/M2/M3/M4、Pro、Max、Ultra。
- 统一内存大小。
- 前端 Python 里的 `torch.backends.mps` 状态。
- ComfyUI venv 里的 `torch.backends.mps` 状态。
- ComfyUI `/object_info` 里的 LTX / Wan 节点和模型下拉列表。

Mac 推荐顺序：

1. `Mac LTX I2V`：默认路线，优先用关键帧控制画面。
2. `Mac LTX T2V`：没有关键帧时使用，随机性更大。
3. `Mac Wan2.2 TI2V-5B 480P/720P`：只给高内存 Apple Silicon，标为实验档。
4. `Wan2.2 A14B`：Mac 上不默认开放，因为 fp8/MPS dtype 兼容风险高；前端预检会拦截。

Mac 一键安装会按档位下载，不会默认拉完整 A14B：

- `mac-low` / `mac-balanced`：只下载 LTX 2B、T5 XXL 和示例关键帧。
- `mac-wan5b`：下载 LTX 2B、T5 XXL、Wan2.2 TI2V-5B、UMT5 和 Wan2.2 VAE。
- `post-only`：只下载 RIFE/超分后期权重。
- `cuda-full`：Windows/NVIDIA 主线，下载完整 Wan2.2、后期和超分资产。

如果前端提示“前端 Python 未安装 torch”，不代表 ComfyUI 不能用 MPS。系统会继续检测 ComfyUI venv；如果你用 ComfyUI Desktop，先启动 Desktop 后刷新环境侦测。

Mac 上建议先跑 `Mac LTX I2V 低档` smoke test，再提高分辨率或切 Wan5B。出现 OOM、dtype、黑屏、节点加载失败时，先退回低分辨率短帧数。

## 模型自选

第一步会自动推荐模型，但后面的步骤都可以在 `当前使用模型` 下拉框里手动切换。切换后会同步：

- 实际提交给后端的 `mode`。
- 步骤右侧显示的模型名。
- 分辨率、帧数、Steps、CFG 等默认参数。
- T2V 模式会隐藏关键帧上传区。
- RIFE 2x / 4x 会同步插帧倍率。
- RealESRGAN 2x / 4x-UltraSharp 4x 会同步目标分辨率显示和实际超分模型。
- 对应的 ComfyUI API workflow 会自动拉取并预检；预检不过不会提交到 ComfyUI 队列。

每次选择模型或调整关键参数时，`当前使用模型` 下方会显示 workflow 状态：

- `ComfyUI workflow 已创建并预检通过`：可以生成。
- `本步骤使用本地 ffmpeg，不需要 ComfyUI 图`：闪烁修复走本地处理。
- `workflow 预检失败`：通常是 ComfyUI 没启动、节点未加载、模型/自定义节点缺失，先回到第一步环境侦测或重启 ComfyUI。

当前前端已经直接接入的可选档位：

- 草稿：`Mac LTX I2V 低档/均衡/质量档`、`Wan2.2 TI2V-5B 720P`、`Wan2.2 TI2V-5B 480P 小显存`。
- 正片：`Mac LTX I2V`、`Mac LTX T2V`、`Mac Wan2.2 TI2V-5B 480P/720P 实验档`、`Wan2.2 I2V-A14B 720P`、`Wan2.2 I2V-A14B 480P`、`Wan2.2 T2V-A14B 720P/480P`、`Wan2.2 TI2V-5B 720P/480P 轻量正片`。
- 后期：`ffmpeg deflicker + hqdn3d`、`RIFE 4.9 2x/4x`。
- 超分：`RealESRGAN x2plus 2x`、`4x-UltraSharp 4x`。

低配或 macOS 上可以参考第一步的路线建议。`LTX-Video` 已接入前端；`AnimateDiff`、`Wan 1.3B` 这类更轻的视频模型目前仍作为路线建议展示。

## 参数

- 横屏：`1280 x 704`
- 竖屏：`704 x 1280`
- 帧率：`24`
- A14B 正片默认：`4 steps`、`CFG 1.0`
- TI2V 草稿默认：`20 steps`、`CFG 5.0`
- `Seed -1` 表示每次随机；填固定数字可以复现同一镜头。
- 闪烁修复默认：`deflicker=s=8:m=pm` 加轻微 `hqdn3d` 时间降噪。
- 清晰度增强默认：`RealESRGAN_x2plus.pth`，输出 2x 分辨率视频。
- 清晰度增强界面会显示输入视频分辨率和 2x 输出目标分辨率，例如 `1280 x 704 -> 2560 x 1408`。

前端每个参数下方都有中文解释：

- `画幅和分辨率`：越大越清晰，也越吃显存和时间。
- `镜头时长`：帧数越多越长，但长镜头更容易漂移。
- `FPS`：24fps 是默认电影感帧率，RIFE 2x 会变成 48fps。
- `Steps`：采样步数越高越慢；A14B 使用 4 步 LoRA。
- `CFG`：提示词强度，过高会更僵硬或更容易出伪影。
- `Seed`：随机种子，固定后方便复现和微调。
- `闪烁修复`：主要修亮度、曝光和轻微纹理闪烁；人物变形、Logo 乱跳、物体变来变去需要回到生成阶段重跑。
- `清晰度增强`：会增加分辨率和文件大小，适合最终交付前处理，不建议在草稿阶段反复跑。

## 输出位置

生成结果会显示在前端右侧，也会保存到：

```text
E:\ai photo creat\output\wan22_frontend
```

上传给前端的图片和视频会保存到：

```text
E:\ai photo creat\input\beginner_frontend
```

## 已修复的错误

之前 ComfyUI 日志里的错误是：

```text
Prompt has no outputs
```

原因是原工作流顶层缺少 `SaveVideo` 输出节点。现在这些工作流已经补上输出节点：

- `E:\ai photo creat\video creat\workflows\ready\01_Wan22_I2V_A14B_720p_4step_READY.json`
- `E:\ai photo creat\video creat\workflows\ready\02_Wan22_T2V_A14B_720p_4step_READY.json`
- `E:\ai photo creat\user\default\workflows\01_Wan22_I2V_A14B_720p_4step_READY.json`
- `E:\ai photo creat\user\default\workflows\02_Wan22_T2V_A14B_720p_4step_READY.json`

前端里的“检查配置”按钮也会验证模型文件、节点和 API 工作流图。
