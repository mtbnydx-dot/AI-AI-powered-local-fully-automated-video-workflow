# Wan2.2 Local Video Workflow

一个面向小白的本地 AI 视频工作流前端。设计目标是：先启动本前端，然后在前端里完成 ComfyUI 安装/启动、节点安装、模型下载和工作流生成。

第一次使用请先看 [START_HERE.md](START_HERE.md)，那里只保留启动、安装、排错和发布前检查的最短路径。

## 运行模式

前端第一步 `环境侦测` 顶部有 `运行入口模式`：

- `本机一体`：前端和后端都在这台机器，适合首次开箱。
- `服务端`：这台机器负责安装 ComfyUI、下载模型和生成视频；保存后重启 `start.bat` / `START_WORKFLOW.bat`，局域网电脑可以访问页面显示的 LAN 地址。
- `客户端`：这台机器只作为控制面板；填写服务端地址和访问令牌后，安装、启动、自检、生成、诊断和媒体预览都会调用服务端 API。

服务端/本机一体会自动生成访问令牌。局域网客户端进行写操作时需要令牌；本机浏览器操作不需要额外配置。这个仓库不包含模型文件，也不会把本地 `.wan22_workflow_config.json` 上传到 GitHub。

Windows / NVIDIA 主线是：

1. 环境侦测
2. 关键帧
3. Wan2.2 TI2V-5B 试镜头
4. Wan2.2 A14B 正式片段
5. 闪烁修复
6. RIFE 插帧
7. RealESRGAN / UltraSharp 清晰度增强
8. 多镜头拼接

macOS Apple Silicon 会进入独立路线：

1. 环境侦测
2. 关键帧
3. Mac LTX I2V 低档/均衡/质量档
4. 高内存 Mac 可选 Wan2.2 TI2V-5B 480P/720P 实验档
5. 闪烁修复、插帧、清晰度增强
6. 多镜头拼接

前端会根据硬件自动推荐模型档位，也允许手动选择保守档、小显存档、A14B 档、T2V 档、RIFE 2x/4x、2x/4x 超分。每次切换模型都会拉取并预检对应 ComfyUI API workflow，预检失败时不会提交到 ComfyUI 队列。

第一步环境侦测会优先读取正在运行的 ComfyUI `--base-directory`、`--input-directory`、`--output-directory`；如果默认启动没有 `--base-directory`，也会尝试从 `/system_stats` 的 `main.py` 路径推断 ComfyUI base。这对 ComfyUI Desktop 和 macOS 很重要：如果 ComfyUI 实际扫描的目录和前端默认目录不同，前端会自动把模型、节点、上传文件和输出预览切到 ComfyUI 正在使用的目录。

## 不包含什么

本仓库不包含模型文件、输出视频、ComfyUI 本地用户数据或虚拟环境。

`.gitignore` 已排除：

- `models/`
- `input/`
- `output/`
- `temp/`
- `custom_nodes/`
- `*.safetensors`
- `*.pth`
- `*.gguf`
- 视频和图片输出文件

## 推荐目录结构

最省心的方式：把本项目放在 ComfyUI 用户/base 目录的子目录里。

```text
ComfyUI-base/
  models/
  input/
  output/
  custom_nodes/
  wan22-local-video-workflow/
```

如果你的项目不在这个位置，可以设置 `COMFY_BASE_DIR` 指向 ComfyUI 用户/base 目录。

如果 ComfyUI 已经在运行，前端会优先使用 ComfyUI 自己报告的目录；如果 ComfyUI 未连接，才会回退到 `COMFY_BASE_DIR`。

未设置 `COMFY_BASE_DIR` 时，启动器会先判断项目父目录是否已经像 ComfyUI base（同时存在 `models/`、`input/`、`output/`、`custom_nodes/`）。只有满足这个条件才使用父目录；否则使用项目目录自己，避免在 macOS 桌面等位置误创建一堆模型目录。

也可以设置 `COMFY_INSTALL_DIR` 指向 ComfyUI 源码安装目录；不设置时默认是：

```text
<COMFY_BASE_DIR>/ComfyUI
```

如果这个默认目录已经非空且不像 ComfyUI 源码目录，前端会自动退回项目内 `ComfyUI/`，避免被同级旧目录卡住。显式设置 `COMFY_INSTALL_DIR` 时会尊重你的配置。

## 环境要求

- Python 3.10-3.12。启动器会自动创建项目 `.venv`、修复缺失 pip 并安装前端依赖。
- ComfyUI 可以还没安装；前端第一步可以安装/更新并启动源码版 ComfyUI。
- ComfyUI/PyTorch 的 `auto` 后端会在 macOS 选择 MPS，检测到 NVIDIA 时选择 CUDA，否则选择 CPU，避免无 NVIDIA 的 Windows/Linux 机器误装 CUDA 大包。
- ComfyUI-VideoHelperSuite、ComfyUI-Frame-Interpolation 和后处理权重会随当前档位一键安装。
- Git 不是硬性前置条件；没有 Git 时安装脚本会改用 GitHub ZIP 下载 ComfyUI 和自定义节点源码，ZIP 下载会使用 `.part` 临时文件并自动重试。已有 Git 时会优先 clone/update；如果首次 `git clone` 失败，脚本会清理半截目录并自动退回 ZIP 下载。
- 如果模型或权重已经放在项目上级目录的 ComfyUI `models/` 里，或者你用 `WAN22_LOCAL_ASSET_DIRS` 指向了本地缓存目录，安装器会先复用同名且大小匹配的本地文件；找不到本地文件时才会下载。
- ffmpeg 用于闪烁修复；系统会按 `FFMPEG_PATH`、系统 PATH、`imageio-ffmpeg` 内置二进制的顺序检测，并在环境页显示来源。
- NVIDIA CUDA 显卡推荐跑 Wan A14B 主线；Apple Silicon 会进入 Mac LTX / Mac Wan5B 实验路线

硬件粗略建议：

- 8GB-12GB CUDA：TI2V-5B 480P 短镜头或另接更小模型工作流
- 16GB-24GB CUDA：TI2V-5B 480P/720P
- 48GB CUDA：默认走 TI2V-5B 720P/Wan5B 保守档；A14B 480P 只建议手动尝试、短镜头、必要时 offload
- 80GB-96GB CUDA：I2V-A14B 720P 主线
- 8GB/16GB Apple Silicon：Mac LTX 低档，512x320 或更低，1-2 秒 smoke test
- 24GB-36GB Apple Silicon：Mac LTX 均衡/质量档，Wan5B 低分辨率入口可见但不默认
- 48GB/64GB Apple Silicon：Mac Wan2.2 TI2V-5B 480P 实验档
- 96GB/128GB+ Apple Silicon：Mac Wan2.2 TI2V-5B 720P 高内存实验档
- Intel Mac / MPS 不可用：只推荐后期、剪辑和低风险流程

## macOS 视频生成模式

环境侦测会把 macOS 变成平台策略，而不是只显示“可实验”：

- `mac_chip`：M1/M2/M3/M4、Pro、Max、Ultra。
- `unified_memory_gb`：统一内存大小。
- `front_torch_mps_ready`：前端 Python 是否有可用 MPS。
- `comfy_torch_mps_ready`：ComfyUI venv 是否有可用 MPS。
- `/object_info`：验证 LTX / Wan 节点和模型下拉列表。

Mac 推荐顺序：

1. LTX 2B I2V：默认路线，优先关键帧控制画面。
2. LTX 2B T2V：没有关键帧时使用。
3. Wan2.2 TI2V-5B：48GB+ 作为实验档，96GB/128GB+ 可尝试 720P 短镜头。
4. Wan2.2 A14B：只保留专家风险提示，不作为 Mac 默认推荐，因为 fp8/MPS dtype 兼容风险较高。

如果前端 Python 没装 torch，不代表 ComfyUI 不能用 MPS。前端会继续检测 ComfyUI 的 Python；使用 ComfyUI Desktop 时，先启动 Desktop 后刷新环境侦测。

## 启动器会自动安装前端依赖

通常不需要手动执行下面命令。第一次运行 `START_WORKFLOW.bat`、`START_WORKFLOW.ps1` 或 `START_WORKFLOW.command` 时，启动器会自动创建 `.venv`，先用 `ensurepip` 修复缺失 pip，再执行 `pip install -r requirements.txt`；pip 下载失败会自动重试。Windows 推荐双击 `.bat`，它会用 `ExecutionPolicy Bypass` 调用 `.ps1`，避免 PowerShell 执行策略拦住第一次启动。如果 Windows 没有 Python 但有 winget，启动器会询问是否安装 Python 3.12，并使用 winget 协议确认参数减少首次安装卡顿；安装后还会额外检查常见 Python 安装目录，减少 PATH 尚未刷新的失败。如果 Windows 没有 winget，会提示官方 Python 下载页。macOS 没有 Python 时，启动器会检查常见 Homebrew/python.org 安装路径；有 Homebrew 时会询问是否安装 `python@3.12`，没有 Homebrew 但有 `curl` 时会询问是否安装 Homebrew，无法自动安装时会提示或打开官方 Python macOS 下载页。启动器安装前端依赖时会复用本地 `.wan22_workflow_config.json` 里的代理和 pip 镜像设置；如果前端依赖安装多次失败，启动器会直接询问是否填写 pip 镜像或 HTTP 代理，保存后自动重试一次。

如果第一次启动卡在前端依赖安装，网页还没打开，优先按启动窗口提示填写 pip 镜像或代理。命令行备用方式如下：

```bash
python START_WORKFLOW.py --set-pip-index https://pypi.org/simple
python START_WORKFLOW.py --set-proxy http://127.0.0.1:7890
python START_WORKFLOW.py --show-download-settings
```

Windows 可把 `python START_WORKFLOW.py` 换成 `START_WORKFLOW.bat`；macOS 可换成 `./START_WORKFLOW.command`。需要清空这些本地设置时运行 `python START_WORKFLOW.py --clear-download-settings`。

如果你想手动准备前端 Python 环境，可以在项目目录运行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python -m ensurepip --upgrade
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

macOS / Linux：

```bash
./.venv/bin/python -m ensurepip --upgrade
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -r requirements.txt
```

如果你已经有 ComfyUI 自带的 Python 环境，也可以直接用那个 Python 启动前端。

启动器会优先使用项目目录里的 `.venv`。如果端口 `7860` 已经被旧版前端占用，新启动器会检测 `/api/health`，不匹配时自动使用下一个空闲端口并打开浏览器。

如果没有手动设置 `COMFY_URL`，启动器和前端会自动尝试本地常见 ComfyUI 端口 `8000` 和 `8188`。这能兼容本前端启动的 ComfyUI，也能兼容 ComfyUI Desktop 或默认端口服务。手动设置 `COMFY_URL` 时，系统会严格使用你的配置。

## 先启动前端

你只需要先让这个前端跑起来。即使 ComfyUI 没安装或没启动，前端也能打开，并在第一步里提示怎么装。

Windows 最简单方式：双击 `START_WORKFLOW.bat`。

Windows PowerShell：

```powershell
.\START_WORKFLOW.ps1
```

macOS / Linux：

```bash
chmod +x ./START_WORKFLOW.command
./START_WORKFLOW.command
```

如果压缩包或网页上传让 `.command` 丢失执行权限，重新运行上面的 `chmod +x` 即可；也可以直接执行 `bash ./START_WORKFLOW.command`。

或直接：

```bash
python START_WORKFLOW.py
```

## 一键自检

不下载模型、不启动真实生成，只检查代码、manifest、workflow 图、无 Git 兜底和发布包卫生：

```bash
python scripts/self_check.py
```

通过时会显示 `SELF CHECK OK`。这个命令可在 GitHub 下载后的项目目录直接运行。

更接近新机器的发布烟测：

```bash
python scripts/release_smoke.py --json
```

它会把发布包复制到临时目录，检查启动器、安装器 dry-run、前端关键 API 和诊断接口；不会下载模型，也不会写入真实 ComfyUI 目录。

如果要进一步验证“本地完全没有项目 `.venv`”时也能首启，可以运行：

```powershell
python scripts/clean_bootstrap_smoke.py --json
```

它会复制发布包到临时目录，真实创建一个全新的 `.venv`、安装前端依赖并启动前端；需要 PyPI 或 pip 镜像可达，但不会下载视频模型。

启动后打开：

```text
http://127.0.0.1:7860
```

如果 ComfyUI 计划使用非默认地址：

```bash
COMFY_URL=http://127.0.0.1:8188 python START_WORKFLOW.py
```

Windows PowerShell：

```powershell
$env:COMFY_URL="http://127.0.0.1:8188"
.\START_WORKFLOW.ps1
```

## 在前端里安装/启动 ComfyUI

进入前端后，第一步 `环境侦测` 里会显示 `ComfyUI 安装与启动`。

你可以直接在页面里操作：

1. 先看 `环境侦测` 给出的硬件策略和推荐安装档位。
2. 小白推荐直接点击 `一键准备环境`：如果 ComfyUI 未连接，它会安装/更新源码版 ComfyUI，按当前档位安装节点、模型、RIFE 和超分权重，然后尝试启动 ComfyUI；如果已经检测到 ComfyUI Desktop 或其他 ComfyUI 正在运行，它会跳过源码版 ComfyUI 安装，把模型和节点安装到运行中的 active base。
3. 如果你想手动控制，也可以分别点击 `安装/更新 ComfyUI`、`启动 ComfyUI`、在 `安装档位` 里选择 `自动推荐`、`CUDA Wan5B 保守档`、`CUDA 完整 Wan2.2 档`、`mac-low`、`mac-balanced`、`mac-wan5b` 或 `仅后期工具`，再点击 `一键安装/修复缺失项`。
4. 安装节点或模型后，如果页面提示需要重启 ComfyUI，就重启 ComfyUI，再回到 `环境侦测` 点击 `重新侦测`。
5. 点击 `生成链路测试`：前端会选择当前环境可用的最保守视频模型，提交一个短视频任务，验证 ComfyUI 队列、模型名、节点和输出目录真的能跑通。

`自动推荐` 会按当前硬件保守选择：NVIDIA 单卡 80GB 以上才默认 `CUDA 完整 Wan2.2 档`；80GB 以下 CUDA 默认 `CUDA Wan5B 保守档`；没有 CUDA 或 Apple Silicon MPS 时默认进入 `仅后期工具` 或 Mac 低档路线。磁盘空间不足或只想先跑通流程时，也可以手动切到 `CUDA Wan5B 保守档`；只做后期时可选 `仅后期工具`。

点击 `运行本机自检` 可以在前端里执行无下载检查：前置条件检测和项目自检会在后台运行，结果会写入日志和诊断包，不需要打开命令行。

`运行本机自检` 不下载模型、不提交生成；`一键准备环境` 会真实安装 ComfyUI、节点和模型；`生成链路测试` 会真实提交一个低成本短视频任务。首次装完后建议先跑 `生成链路测试`，通过后再进入关键帧和正式分镜。

真实安装开始前会先通过 HTTPS 检查本次档位实际需要的下载源，可能包括 GitHub、Hugging Face、PyPI 和 PyTorch 下载源。已经就绪的模型/节点不会触发不必要的站点阻断；前端显示 `本地缓存可用` 的模型也不会触发 Hugging Face 阻断，安装时会先复用本地文件。如果本次真正需要的下载源不可达，前端会直接停止任务并给出代理、DNS、防火墙和断点续传建议，不会等到大模型下载中途才报错。ComfyUI、PyTorch 和自定义节点 requirements 的 pip 安装会先确认 pip 可用，必要时自动 `ensurepip`，再执行重试安装。

一键安装前会做磁盘空间预检：完整文件会跳过，`.part` 续传文件只计算剩余体积，并额外预留 10GB 缓冲。空间不足时前端会禁用安装按钮，后端接口也会直接返回失败任务，不会开始下载。此时可以降档、清理磁盘，或把 `COMFY_BASE_DIR` 指向更大的 ComfyUI base 目录。

如果模型下载中断，保留的 `.part` 半截文件会在前端缺失项里显示“已下载/剩余/可断点续传”。再次点击 `一键安装/修复缺失项` 会优先从剩余字节继续下载；如果 `.part` 实际已经下载完整，安装脚本会直接把它改名成正式模型文件，不再重复请求网络。

如果 Hugging Face、PyPI 或 PyTorch 下载源在你的网络里不可达，可以在第 1 步 `环境侦测` 的 `下载源与代理` 里填写 Hugging Face 镜像地址、pip 镜像地址，或本机 HTTP 代理，例如 `http://127.0.0.1:7890`。这些设置会保存到本地 `.wan22_workflow_config.json`，不会进入 GitHub；一键准备、ComfyUI 安装、PyTorch/pip 依赖安装、安装缺失项、网络预检和诊断都会使用同一套网络设置。诊断信息会脱敏代理里的账号密码。高级用户也可以用环境变量：

填写下载源或代理后，可以直接点击 `测试下载源`。它会先保存当前输入，但不会安装 ComfyUI、不会下载模型，只会按当前设置测试 GitHub、Hugging Face 或镜像、PyPI 和 PyTorch 下载源，并把结果刷新到前置条件列表里。第一次准备环境前建议先点一次，尤其是 Hugging Face 在你当前网络里不稳定时。

```bash
WAN22_HF_ENDPOINT=https://your-hf-endpoint.example python START_WORKFLOW.py
```

或：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 python START_WORKFLOW.py
```

或：

```bash
PIP_INDEX_URL=https://pypi.org/simple python START_WORKFLOW.py
```

环境变量优先级高于页面保存的设置；页面会显示当前生效来源。

如果安装任务失败，前端日志会自动显示失败类型和下一步建议；点击右侧 `下载诊断包` 或 `复制诊断信息` 时，也会带上最近安装任务的失败分类和日志尾部，方便远程排查。诊断包会脱敏代理地址里的账号密码。

安装/更新 ComfyUI 也会做空间预检。CUDA 后端建议至少预留 20GB，Mac/CPU 后端建议至少预留 8GB；如果空间不足，请清理磁盘，或用 `COMFY_INSTALL_DIR` 指向更大的安装盘。

安装 ComfyUI 使用的脚本是：

```text
scripts/install_comfyui.py
```

默认 PyTorch 后端为 `auto`：

- macOS：选择 MPS/普通 PyTorch。
- Windows/Linux 检测到 NVIDIA：选择 CUDA PyTorch。
- Windows/Linux 未检测到 NVIDIA：选择 CPU PyTorch，避免无显卡机器误装 CUDA 大包。

高级用户也可以手动运行：

```bash
python scripts/install_comfyui.py --base-dir "<COMFY_BASE_DIR>" --install-dir "<COMFY_INSTALL_DIR>" --backend auto
```

只预演安装计划和磁盘空间，不实际安装：

```bash
python scripts/install_comfyui.py --base-dir "<COMFY_BASE_DIR>" --install-dir "<COMFY_INSTALL_DIR>" --backend auto --dry-run
```

`--backend` 可选：`auto`、`cuda`、`cpu`、`mps`、`skip`。`auto` 会在 macOS 选择 MPS，检测到 NVIDIA 时选择 CUDA，否则选择 CPU。

## 发布前验收

上传 GitHub 前建议在发布目录运行：

```bash
python scripts/prerequisite_doctor.py --json
python scripts/self_check.py --json
python scripts/release_smoke.py --json
python scripts/clean_bootstrap_smoke.py --json
python START_WORKFLOW.py --check
```

Windows 还可以额外检查双击入口：

```powershell
.\START_WORKFLOW.bat --check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\START_WORKFLOW.ps1 --check
```

这些检查不会下载模型，也不会启动真实生成。

## 下载模型和工作流资产

先确认 `COMFY_BASE_DIR` 指向 ComfyUI 用户/base 目录。项目放在 base 目录子文件夹时可以不设置。

Windows PowerShell 示例：

```powershell
$env:COMFY_BASE_DIR="D:\ComfyUI"
.\.venv\Scripts\python .\scripts\install_workflow_assets.py --base-dir $env:COMFY_BASE_DIR
```

macOS / Linux 示例：

```bash
export COMFY_BASE_DIR="$HOME/ComfyUI"
./.venv/bin/python ./scripts/install_workflow_assets.py --base-dir "$COMFY_BASE_DIR"
```

如果你知道 ComfyUI 自己的 Python 路径，建议传给安装脚本，这样会自动安装自定义节点 requirements：

```bash
./.venv/bin/python ./scripts/install_workflow_assets.py \
  --base-dir "$COMFY_BASE_DIR" \
  --comfy-python "$COMFY_INSTALL_DIR/.venv/bin/python"
```

这个脚本支持安装档位：

```bash
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile mac-low
```

建议先预演一次，不下载文件，只查看当前档位、预计下载体积和磁盘空间：

```bash
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile mac-low --dry-run
```

可选 `--profile`：

- `cuda-full`：Windows/NVIDIA 主线，下载完整 Wan2.2、RIFE 和超分资产。
- `cuda-wan5b`：Windows/NVIDIA 保守档，下载 TI2V-5B、UMT5、Wan VAE、RIFE 和超分资产，不下载 A14B。
- `mac-low` / `mac-balanced`：下载 LTX 2B、T5 XXL、RIFE/超分后期权重、后处理节点和示例关键帧。
- `mac-wan5b`：下载 LTX 2B、T5 XXL、Wan2.2 TI2V-5B、UMT5、Wan2.2 VAE、RIFE/超分后期权重和后处理节点。
- `post-only`：只下载 RIFE/超分后期权重。

前端的一键安装默认会自动选择档位，也可以在页面里手动切换；手动运行脚本时才需要显式传 `--profile`。

`cuda-full` 会下载或校验：

- Wan2.2 TI2V-5B
- Wan2.2 I2V-A14B fp8 high/low
- Wan2.2 T2V-A14B fp8 high/low
- UMT5 文本编码器
- Wan VAE
- Lightx2v 4-step LoRA
- RIFE 4.9 权重
- RealESRGAN x2plus
- 4x-UltraSharp
- 示例关键帧
- 缺失的两个 ComfyUI 自定义节点仓库

下载完成后，重启 ComfyUI，让模型列表和自定义节点重新加载。

## 使用流程

1. 启动前端。
2. 在 `环境侦测` 中点击 `一键准备环境`，或者手动安装/启动 ComfyUI 并补齐当前档位资产。
3. 点击 `生成链路测试`，确认已经能真实出一个短视频。
4. 在 `关键帧` 上传或确认首帧图片。
5. Windows/CUDA 用 `TI2V-5B` 生成草稿；Mac 用 `LTX I2V` 先跑低分辨率 smoke test。
6. Windows/CUDA 用 `I2V-A14B` 或自选档生成正式片段；高内存 Mac 可试 `Wan5B` 实验档。
7. 视情况做闪烁修复、RIFE 插帧、清晰度增强。
8. 多个 3-6 秒镜头导入剪映、DaVinci Resolve 或 Premiere Pro 拼接。

输出默认保存到：

```text
<COMFY_BASE_DIR>/output/wan22_frontend
```

上传的图片和视频默认保存到：

```text
<COMFY_BASE_DIR>/input/beginner_frontend
```

## 工作流文件

`workflows/ready/` 里包含可直接导入 ComfyUI 的参考工作流。

`workflows/mac/` 里包含 Mac LTX I2V/T2V、LTX smoke test、Mac Wan5B 480P API workflow 样例。

`workflows/comfyui_blueprints/` 里保留官方/蓝图版本，方便对照和二次编辑。

前端实际提交时会动态生成 ComfyUI API workflow，并在提交前做节点、模型名、输出节点和 Mac/MPS 风险预检。

## 常见问题

### 前端提示 ComfyUI 未连接

先回到第 1 步 `环境侦测`。推荐顺序是：点击 `一键准备环境`；如果已经装好但未运行，就点 `启动 ComfyUI` 并等状态变成“已连接”；最后点击 `生成链路测试`。如果你手动改过端口，再确认 `COMFY_URL` 是否指向正在运行的 ComfyUI。

### workflow 预检失败

常见原因：

- ComfyUI 没启动
- 自定义节点没安装或没重启
- 模型文件缺失
- ComfyUI 版本节点名变化

先回到第一步 `环境侦测` 查看缺失项。

### 需要别人帮忙排查

点击右侧结果区的 `下载诊断包`，把下载的 JSON 文件发给维护者；也可以点 `复制诊断信息` 直接粘贴到聊天窗口。它会汇总前端版本、ComfyUI 连接状态、硬件判断、安装档位、缺失项、磁盘预检、加载诊断和最近任务日志尾部，并会脱敏代理地址里的账号密码。

### macOS / ComfyUI Desktop 下载了但没有加载

先看第一步 `加载诊断`：

- 如果显示目录不一致，说明文件下载到了一个目录，而 ComfyUI 实际扫描另一个目录。重新点击 `一键安装/修复缺失项`，前端会安装到运行中的 ComfyUI active base。
- 如果显示自定义节点目录存在但节点未加载，通常是节点 requirements 没装进 ComfyUI Python 环境，或安装后没有重启 ComfyUI。重新点击 `一键安装/修复缺失项`，完成后重启 ComfyUI。
- 如果模型文件已存在但没有出现在 ComfyUI 模型列表，通常需要重启 ComfyUI；仍不出现时检查 `加载诊断` 里的 active base/input/output 是否就是你当前 ComfyUI 使用的目录。

ComfyUI Desktop 管理 Python 环境的方式可能和源码版不同。如果脚本提示找不到 ComfyUI Python，请在 Desktop 的自定义节点管理器里安装节点依赖，或改用前端的 `安装/更新 ComfyUI` 创建源码版 ComfyUI。

如果 ComfyUI Desktop 已经连接，`一键准备环境` 默认不会再安装另一份源码版 ComfyUI，也不会把本项目源码版 ComfyUI 的 Python 当成 Desktop 的节点环境。模型和节点会放入 Desktop 正在使用的 active base；节点依赖仍可能需要在 Desktop 管理器中安装并重启。

### 48GB 显卡能不能跑 A14B 720P

默认不推荐。48GB 更适合 A14B 480P、短镜头、offload，或者用 TI2V-5B 720P 做主力。

### 双 48GB 是否等于一张 96GB

默认不是。大多数单个 ComfyUI 视频任务主要看单卡显存。双卡更适合并行跑镜头，或做更复杂的多进程配置。
