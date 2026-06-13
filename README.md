# Wan2.2 Local Video Workflow

一个面向小白的本地 AI 视频工作流前端。设计目标是：先启动本前端，然后在前端里完成 ComfyUI 安装/启动、节点安装、模型下载和工作流生成。

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

第一步环境侦测会优先读取正在运行的 ComfyUI `--base-directory`、`--input-directory`、`--output-directory`。这对 ComfyUI Desktop 和 macOS 很重要：如果 ComfyUI 实际扫描的目录和前端默认目录不同，前端会自动把模型、节点、上传文件和输出预览切到 ComfyUI 正在使用的目录。

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

## 环境要求

- Python 3.10-3.12
- 已运行的 ComfyUI，默认地址 `http://127.0.0.1:8000`
- ComfyUI 已安装或可安装：
  - ComfyUI-VideoHelperSuite
  - ComfyUI-Frame-Interpolation
- ffmpeg，用于闪烁修复
- NVIDIA CUDA 显卡推荐跑 Wan A14B 主线；Apple Silicon 会进入 Mac LTX / Mac Wan5B 实验路线

硬件粗略建议：

- 8GB-12GB CUDA：TI2V-5B 480P 短镜头或另接更小模型工作流
- 16GB-24GB CUDA：TI2V-5B 480P/720P
- 48GB CUDA：TI2V-5B 720P，A14B 建议 480P/短镜头/offload
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

## 安装 Python 依赖

在项目目录运行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

macOS / Linux：

```bash
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -r requirements.txt
```

如果你已经有 ComfyUI 自带的 Python 环境，也可以直接用那个 Python 启动前端。

启动器会优先使用项目目录里的 `.venv`，其次才使用 `COMFY_BASE_DIR/.venv`，最后才尝试系统 Python。找不到 `uvicorn` 或 Python 版本不在 3.10-3.12 时，会在终端打印安装命令。

## 先启动前端

你只需要先让这个前端跑起来。即使 ComfyUI 没安装或没启动，前端也能打开，并在第一步里提示怎么装。

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

1. 点击 `安装/更新 ComfyUI`：会 clone/update ComfyUI，创建 `.venv`，安装 PyTorch 和 ComfyUI 依赖。
2. 点击 `启动 ComfyUI`：会用安装好的 ComfyUI 启动本地服务。
3. 点击 `一键安装/修复缺失项`：安装工作流需要的自定义节点、Wan2.2 模型、RIFE、超分权重；如果文件已下载但没有加载，也会重新安装自定义节点 requirements。
4. 安装节点或模型后，重启 ComfyUI。

安装 ComfyUI 使用的脚本是：

```text
scripts/install_comfyui.py
```

默认 PyTorch 后端为 `auto`：

- Windows/Linux：默认走 CUDA 安装。
- macOS：默认走 MPS/普通 PyTorch 安装。

高级用户也可以手动运行：

```bash
python scripts/install_comfyui.py --base-dir "<COMFY_BASE_DIR>" --install-dir "<COMFY_INSTALL_DIR>" --backend auto
```

`--backend` 可选：`auto`、`cuda`、`cpu`、`mps`、`skip`。

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

可选 `--profile`：

- `cuda-full`：Windows/NVIDIA 主线，下载完整 Wan2.2、RIFE 和超分资产。
- `mac-low` / `mac-balanced`：只下载 LTX 2B、T5 XXL 和示例关键帧。
- `mac-wan5b`：下载 LTX 2B、T5 XXL、Wan2.2 TI2V-5B、UMT5 和 Wan2.2 VAE。
- `post-only`：只下载 RIFE/超分后期权重。

前端的一键安装会自动选择档位；手动运行脚本时才需要显式传 `--profile`。

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
2. 在 `环境侦测` 中安装/启动 ComfyUI。
3. 点击 `一键安装/修复缺失项` 补齐当前硬件档位需要的模型、节点和后期权重。
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

先启动 ComfyUI，再确认 `COMFY_URL` 是否正确。

### workflow 预检失败

常见原因：

- ComfyUI 没启动
- 自定义节点没安装或没重启
- 模型文件缺失
- ComfyUI 版本节点名变化

先回到第一步 `环境侦测` 查看缺失项。

### macOS / ComfyUI Desktop 下载了但没有加载

先看第一步 `加载诊断`：

- 如果显示目录不一致，说明文件下载到了一个目录，而 ComfyUI 实际扫描另一个目录。重新点击 `一键安装/修复缺失项`，前端会安装到运行中的 ComfyUI active base。
- 如果显示自定义节点目录存在但节点未加载，通常是节点 requirements 没装进 ComfyUI Python 环境，或安装后没有重启 ComfyUI。重新点击 `一键安装/修复缺失项`，完成后重启 ComfyUI。
- 如果模型文件已存在但没有出现在 ComfyUI 模型列表，通常需要重启 ComfyUI；仍不出现时检查 `加载诊断` 里的 active base/input/output 是否就是你当前 ComfyUI 使用的目录。

ComfyUI Desktop 管理 Python 环境的方式可能和源码版不同。如果脚本提示找不到 ComfyUI Python，请在 Desktop 的自定义节点管理器里安装节点依赖，或改用前端的 `安装/更新 ComfyUI` 创建源码版 ComfyUI。

### 48GB 显卡能不能跑 A14B 720P

默认不推荐。48GB 更适合 A14B 480P、短镜头、offload，或者用 TI2V-5B 720P 做主力。

### 双 48GB 是否等于一张 96GB

默认不是。大多数单个 ComfyUI 视频任务主要看单卡显存。双卡更适合并行跑镜头，或做更复杂的多进程配置。
