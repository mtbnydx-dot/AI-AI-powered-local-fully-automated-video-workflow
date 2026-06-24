# Wan2.2 小白前端使用指南

## 启动

Windows 推荐直接双击：

```text
START_WORKFLOW.bat
```

如果你习惯 PowerShell，也可以运行统一入口：

```powershell
.\START_WORKFLOW.ps1
```

macOS 迁移后用：

```bash
cd "/path/to/wan22-local-video-workflow"
chmod +x START_WORKFLOW.command
./START_WORKFLOW.command
```

统一入口会自动创建项目 `.venv`、安装前端依赖、检查 ComfyUI 是否连得上，然后启动小白前端并打开 `http://127.0.0.1:7860`。Windows 的 `.bat` 会自动用 `ExecutionPolicy Bypass` 调用 `.ps1`，避免第一次双击被 PowerShell 策略拦住。如果 Windows 没有 Python 但有 winget，启动器会询问是否安装 Python 3.12，并且会额外检查 Python 常见安装目录，减少安装后 PATH 没刷新的失败。macOS 没有 Python 但有 Homebrew 时，启动器会询问是否安装 Python 3.12。安装前端依赖前会自动运行 `ensurepip` 修复缺失的 pip，pip 下载失败会重试；如果重试后仍失败，启动器会在窗口里询问是否填写 pip 镜像或 HTTP 代理，保存后自动再试一次。ComfyUI 还没启动时前端仍然会打开，第一步会提示缺什么。

如果第一次启动停在“安装前端依赖”，说明浏览器前端还没起来；启动器已经尝试过 `ensurepip` 和 pip 重试，多半是 pip 网络或代理问题。优先按启动窗口提示填写 pip 镜像或代理；命令行备用方式如下：

```powershell
python START_WORKFLOW.py --set-pip-index https://pypi.org/simple
python START_WORKFLOW.py --set-proxy http://127.0.0.1:7890
python START_WORKFLOW.py --show-download-settings
```

Windows 可把 `python START_WORKFLOW.py` 换成 `.\START_WORKFLOW.bat`，macOS 可换成 `./START_WORKFLOW.command`。这些设置写入本地 `.wan22_workflow_config.json`，不会进入 GitHub 发布包；清空时运行 `python START_WORKFLOW.py --clear-download-settings`。

如果没有手动设置 `COMFY_URL`，启动器和前端会自动尝试本地常见 ComfyUI 端口 `8000` 和 `8188`。这能兼容前端自己启动的 ComfyUI，也能兼容 ComfyUI Desktop 或默认端口服务。你手动设置了 `COMFY_URL` 时，系统会严格使用你的配置。

旧入口只是兼容包装，也会转到统一入口：

```powershell
.\scripts\start_beginner_frontend.ps1
```

## 前后端分离和局域网模式

第一次进入第 1 步 `环境侦测`，页面顶部会出现 `运行入口模式`。这个面板决定当前机器扮演什么角色：

- `本机一体`：默认小白模式。前端、安装器、ComfyUI 启动、模型下载和生成任务都在这台机器上。
- `服务端`：这台机器作为局域网生成服务器。保存后重启 `start.bat` 或 `START_WORKFLOW.bat`，前端会监听 `0.0.0.0`，页面会显示局域网地址。
- `客户端`：这台机器只开控制面板。填写服务端地址后，所有 `/api` 请求会自动转发到服务端，包括一键安装、启动 ComfyUI、自检、生成、诊断和输出预览。

服务端/本机一体模式会自动生成 `访问令牌`。局域网客户端执行写操作时需要填同一个令牌；本机浏览器操作不会因为令牌丢失而卡住。客户端模式下，右侧视频预览和输出链接会自动使用服务端地址，不需要手动改链接。

推荐使用方式：

1. 生成机器上先选择 `服务端` 或 `本机一体`，保存后重启入口。
2. 在服务端页面复制 `局域网地址` 和 `访问令牌`。
3. 其他电脑启动同一个前端，选择 `客户端`，填入服务端地址和令牌。
4. 点击 `刷新连接信息`，确认服务端和 ComfyUI 状态后再操作一键安装或生成。

## 一键自检

不下载模型、不启动真实生成，只检查代码、manifest、工作流图、无 Git 兜底和发布包卫生：

```powershell
python scripts/self_check.py
```

GitHub 上传目录里也可以直接运行同一条命令。

如果要模拟“别人刚下载 GitHub 包”的启动链路，可以运行：

```powershell
python scripts/release_smoke.py --json
```

它会把发布包复制到临时目录，检查启动器、安装器 dry-run、前端关键 API 和诊断接口；不会下载模型，也不会写入真实 ComfyUI 目录。

如果要进一步验证“本地完全没有项目 `.venv`”时也能首启，可以运行：

```powershell
python scripts/clean_bootstrap_smoke.py --json
```

它会复制发布包到临时目录，真实创建一个全新的 `.venv`、安装前端依赖并启动前端；需要 PyPI 或 pip 镜像可达，但不会下载视频模型。

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
- ComfyUI runtime：会显示当前可启动的是源码版 ComfyUI、ComfyUI Desktop bundled runtime，还是只能启动 Desktop 应用；也会显示实际 `main.py` 和 Python 路径，方便确认下载目录和加载目录是不是同一套。
- ComfyUI 安装空间：安装/更新 ComfyUI 和 PyTorch 前会显示安装磁盘剩余空间。
- ffmpeg：闪烁修复需要 `deflicker` 和 `hqdn3d` 滤镜；系统会按 `FFMPEG_PATH`、系统 PATH、`imageio-ffmpeg` 内置二进制的顺序检测，并在环境页显示来源。
- 模型推荐：关键帧、TI2V 草稿、A14B 正片、T2V、闪烁修复、RIFE、超分、剪辑分别显示可用模型。
- 缺失项：模型、RIFE 权重、超分权重、自定义节点。
- 磁盘空间：显示目标目录所在磁盘、当前剩余空间、预计还要下载多少，以及建议预留空间。
- 低配 / mac 路线：8GB-12GB CUDA、16GB-24GB、48GB、80GB+、Apple Silicon、CPU 会分别给建议。

如果有缺失项，先在 `安装档位` 里选择下载范围，再点击 `一键安装/修复缺失项`。常用档位：

- `自动推荐`：按当前硬件选择，适合第一次使用。
- `CUDA Wan5B 保守档`：只下载 TI2V-5B、UMT5、Wan VAE 和后期工具，适合磁盘空间不够或想先跑通流程。
- `CUDA 完整 Wan2.2 档`：下载完整 Wan2.2 A14B/Wan5B 主线、RIFE 和超分资产。
- `mac-low` / `mac-balanced`：下载 Mac LTX 路线和后期工具，不下载 A14B。
- `mac-wan5b`：高内存 Apple Silicon 的 Wan2.2 TI2V-5B 实验档。
- `仅后期工具`：只安装 RIFE/超分和示例关键帧，适合先做闪烁修复、插帧、超分。

`自动推荐` 的规则比较保守：NVIDIA 单卡 80GB 以上才默认 `CUDA 完整 Wan2.2 档`；80GB 以下 CUDA 默认 `CUDA Wan5B 保守档`；没有 CUDA 或 Apple Silicon MPS 时默认进入 `仅后期工具` 或 Mac 低档路线。你仍然可以手动切换到其他档位。

填写下载源或代理后，可以直接点击 `测试下载源`。它会先保存当前输入，但不会安装 ComfyUI、不会下载模型，只会按当前 Hugging Face / pip / 代理设置测试 GitHub、Hugging Face 或镜像、PyPI 和 PyTorch 下载源，并把结果刷新到前置条件列表里。

没有 Git 时，前端安装器会自动改用 GitHub ZIP 下载 ComfyUI 和自定义节点源码；ZIP 下载会使用 `.part` 临时文件并自动重试。已有 Git 时会优先 clone/update。ComfyUI、PyTorch 和自定义节点 requirements 的 pip 安装会先确认 pip 可用，必要时自动 `ensurepip`，再执行重试安装。

如果模型或权重已经放在项目上级目录的 ComfyUI `models/` 里，或者你用 `WAN22_LOCAL_ASSET_DIRS` 指向了本地缓存目录，安装器会先复用同名且大小匹配的本地文件。前端缺失项会显示 `本地缓存可用`，这类文件不会再触发 Hugging Face 网络预检。

下载前会做磁盘空间预检。预检按“还没下载完的文件”计算，已经完整的模型会跳过，`.part` 续传文件只计算剩余部分，并额外预留 10GB 缓冲。空间不足时，一键安装按钮会禁用；可以先切到 `CUDA Wan5B 保守档` 或 `仅后期工具`，也可以清理磁盘，或把 `COMFY_BASE_DIR` 指向空间更大的 ComfyUI base 目录后重新启动前端。

ComfyUI / PyTorch 安装也会做空间预检：`auto` 会在 macOS 选择 MPS，检测到 NVIDIA 时选择 CUDA，否则选择 CPU；CUDA 后端建议至少预留 20GB，Mac/CPU 后端建议至少预留 8GB。如果这一步显示空间不足，请把 `COMFY_INSTALL_DIR` 指向空间更大的磁盘，或清理系统盘后再点安装。

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

Mac 一键安装会按档位下载，不会默认拉完整 A14B；同时会补齐后处理需要的小权重和节点：

- `mac-low` / `mac-balanced`：下载 LTX 2B、T5 XXL、RIFE/超分后期权重、后处理节点和示例关键帧。
- `mac-wan5b`：下载 LTX 2B、T5 XXL、Wan2.2 TI2V-5B、UMT5、Wan2.2 VAE、RIFE/超分后期权重和后处理节点。
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
- `闪烁修复`：主要修亮度、曝光和轻微纹理闪烁；人物变形、Logo 乱跳、物体变来变去需要回到生成阶段重跑。生成失败不会解锁下一步，前端会显示 ComfyUI 节点错误。
- `清晰度增强`：会增加分辨率和文件大小，适合最终交付前处理，不建议在草稿阶段反复跑。

## 输出位置

生成结果会显示在前端右侧，也会保存到：

```text
<ComfyUI base>\output\wan22_frontend
```

上传给前端的图片和视频会保存到：

```text
<ComfyUI base>\input\beginner_frontend
```

`<ComfyUI base>` 是前端环境页显示的 ComfyUI base 目录；如果你没有手动设置 `COMFY_BASE_DIR`，系统会按当前机器自动选择。

## 已修复的错误

之前 ComfyUI 日志里的错误是：

```text
Prompt has no outputs
```

原因是原工作流顶层缺少 `SaveVideo` 输出节点。现在这些工作流已经补上输出节点：

- `workflows\ready\01_Wan22_I2V_A14B_720p_4step_READY.json`
- `workflows\ready\02_Wan22_T2V_A14B_720p_4step_READY.json`
- `<ComfyUI base>\user\default\workflows\01_Wan22_I2V_A14B_720p_4step_READY.json`
- `<ComfyUI base>\user\default\workflows\02_Wan22_T2V_A14B_720p_4step_READY.json`

前端里的“检查配置”按钮也会验证模型文件、节点和 API 工作流图。

如果需要把问题发给别人排查，右侧结果区有 `复制诊断信息` 按钮。它会汇总前端版本、ComfyUI 连接状态、硬件判断、安装档位、缺失项、磁盘预检和加载诊断，不会读取环境变量或账号密钥。
