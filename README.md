# Wan2.2 Local Video Workflow

一个面向小白的本地 AI 视频工作流前端，主线是：

1. 环境侦测
2. 关键帧
3. Wan2.2 TI2V-5B 试镜头
4. Wan2.2 A14B 正式片段
5. 闪烁修复
6. RIFE 插帧
7. RealESRGAN / UltraSharp 清晰度增强
8. 多镜头拼接

前端会根据硬件自动推荐模型档位，也允许手动选择保守档、小显存档、A14B 档、T2V 档、RIFE 2x/4x、2x/4x 超分。每次切换模型都会拉取并预检对应 ComfyUI API workflow，预检失败时不会提交到 ComfyUI 队列。

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

## 环境要求

- Python 3.10+
- 已运行的 ComfyUI，默认地址 `http://127.0.0.1:8000`
- ComfyUI 已安装或可安装：
  - ComfyUI-VideoHelperSuite
  - ComfyUI-Frame-Interpolation
- ffmpeg，用于闪烁修复
- NVIDIA CUDA 显卡推荐；macOS 可以打开前端并做环境侦测，但 Wan 视频模型在 MPS 上通常更慢、更挑版本

硬件粗略建议：

- 8GB-12GB CUDA：TI2V-5B 480P 短镜头或另接更小模型工作流
- 16GB-24GB CUDA：TI2V-5B 480P/720P
- 48GB CUDA：TI2V-5B 720P，A14B 建议 480P/短镜头/offload
- 80GB-96GB CUDA：I2V-A14B 720P 主线
- Apple Silicon：优先关键帧、剪辑、ffmpeg 后期，TI2V-5B 480P 可作为实验档

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

这个脚本会下载或校验：

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

## 启动

先启动 ComfyUI，并确认浏览器能打开：

```text
http://127.0.0.1:8000
```

然后启动本项目。

Windows PowerShell：

```powershell
.\START_WORKFLOW.ps1
```

macOS / Linux：

```bash
chmod +x ./START_WORKFLOW.command
./START_WORKFLOW.command
```

或直接：

```bash
python START_WORKFLOW.py
```

启动后打开：

```text
http://127.0.0.1:7860
```

如果 ComfyUI 不在默认地址：

```bash
COMFY_URL=http://127.0.0.1:8188 python START_WORKFLOW.py
```

Windows PowerShell：

```powershell
$env:COMFY_URL="http://127.0.0.1:8188"
.\START_WORKFLOW.ps1
```

## 使用流程

1. 打开前端后先看 `环境侦测`。
2. 如果缺模型或节点，点击 `一键安装缺失项`，安装后重启 ComfyUI。
3. 在 `关键帧` 上传或确认首帧图片。
4. 用 `TI2V-5B` 生成草稿。
5. 用 `I2V-A14B` 或其他自选档生成正式片段。
6. 视情况做闪烁修复、RIFE 插帧、清晰度增强。
7. 多个 3-6 秒镜头导入剪映、DaVinci Resolve 或 Premiere Pro 拼接。

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

`workflows/comfyui_blueprints/` 里保留官方/蓝图版本，方便对照和二次编辑。

前端实际提交时会动态生成 ComfyUI API workflow，并在提交前做节点和输出节点预检。

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

### 48GB 显卡能不能跑 A14B 720P

默认不推荐。48GB 更适合 A14B 480P、短镜头、offload，或者用 TI2V-5B 720P 做主力。

### 双 48GB 是否等于一张 96GB

默认不是。大多数单个 ComfyUI 视频任务主要看单卡显存。双卡更适合并行跑镜头，或做更复杂的多进程配置。
