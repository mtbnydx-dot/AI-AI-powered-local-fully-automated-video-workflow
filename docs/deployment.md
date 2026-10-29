<!--
<document version="0.2.0" updated_at="2026-10-28T21:51:26+08:00" keywords="离线部署,bundle,wheelhouse,ComfyUI,vLLM,B200" />
-->

# 离线部署

应用、ComfyUI、vLLM 分别使用独立 Python 环境。当前可导出的 CPU bundle 包含应用源码、配置、样例、文档和依赖 wheel；不包含 GPU 框架、CUDA、模型或旧代码。

## 应用 bundle

在线开发机执行，导出的是当前工作目录中的新代码，不要求先提交 Git：

```bash
.venv/bin/python scripts/export_bundle.py --output /tmp/videopipe-cpu.tar.gz
```

在具有相同 Linux 架构和 Python 小版本的离线服务器解压安装。服务器需已具备 Python 与 venv；FFmpeg 由应用依赖自带，无需另装 ffprobe。

```bash
mkdir -p /mnt/video-app
tar -xzf /mnt/releases/videopipe-cpu.tar.gz -C /mnt/video-app
cd /mnt/video-app/videopipe
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse videopipe
.venv/bin/videopipe run examples/product/package.json \
  --config configs/cpu.yaml --output /mnt/video-runs/cpu-example --auto-approve
```

`requirements.lock` 固定应用 CPU 依赖。bundle 不携带 `.venv`、Git 历史、历史运行或模型。CPU 输出是参考图运动预览，用于验证生产流程。

## 在线准备 GPU 运行资源

以下命令供后续 GPU 部署使用，本轮没有执行模型下载或 GPU 推理。准备机应与离线节点保持相同 Linux、CPU 架构、Python 版本和所选 CUDA 环境。运行环境在离线节点就地重建，避免搬迁带绝对路径的 venv。

ComfyUI 基准固定为 `79cf64c21a1dba50749945ba9fe2e3a692f45438`。本项目 H3 默认量化权重按官方说明使用 PyTorch cu130；模型与 workflow 细节见 [ComfyUI 接入](comfyui.md)。[官方模型说明](https://huggingface.co/Comfy-Org/MiniMax-H3)

```bash
export VIDEO_RUNTIME=/mnt/video-runtime
mkdir -p "$VIDEO_RUNTIME/wheels/comfyui" "$VIDEO_RUNTIME/wheels/vllm"
git clone https://github.com/Comfy-Org/ComfyUI.git "$VIDEO_RUNTIME/ComfyUI"
git -C "$VIDEO_RUNTIME/ComfyUI" checkout 79cf64c21a1dba50749945ba9fe2e3a692f45438
python3.12 -m venv "$VIDEO_RUNTIME/comfy-build"
"$VIDEO_RUNTIME/comfy-build/bin/python" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
"$VIDEO_RUNTIME/comfy-build/bin/python" -m pip install -r "$VIDEO_RUNTIME/ComfyUI/requirements.txt"
"$VIDEO_RUNTIME/comfy-build/bin/python" -m pip list --format=freeze > "$VIDEO_RUNTIME/comfyui.lock"
"$VIDEO_RUNTIME/comfy-build/bin/python" -m pip wheel -r "$VIDEO_RUNTIME/comfyui.lock" \
  --extra-index-url https://download.pytorch.org/whl/cu130 -w "$VIDEO_RUNTIME/wheels/comfyui"
```

vLLM 部署基准为 [`0.32.0`](https://github.com/vllm-project/vllm/releases/tag/v0.32.0)，独立采用官方 cu129 依赖。在线安装后预先下载 FlashInfer kernels，再一起固化到 wheelhouse，避免首次离线启动时缺少运行资源。[vLLM 安装说明](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/)

```bash
python3.12 -m venv "$VIDEO_RUNTIME/vllm-build"
"$VIDEO_RUNTIME/vllm-build/bin/python" -m pip install 'vllm==0.32.0' \
  --extra-index-url https://download.pytorch.org/whl/cu129
"$VIDEO_RUNTIME/vllm-build/bin/vllm" download-kernels
"$VIDEO_RUNTIME/vllm-build/bin/python" -m pip list --format=freeze > "$VIDEO_RUNTIME/vllm.lock"
"$VIDEO_RUNTIME/vllm-build/bin/python" -m pip wheel -r "$VIDEO_RUNTIME/vllm.lock" \
  --extra-index-url https://download.pytorch.org/whl/cu129 \
  --extra-index-url https://flashinfer.ai/whl \
  --extra-index-url https://flashinfer.ai/whl/cu129 -w "$VIDEO_RUNTIME/wheels/vllm"
```

使用 Hugging Face CLI 在在线侧下载完整 Qwen 模型目录；H3 下载四个指定文件到 ComfyUI 的模型目录。下载工具不属于生产应用依赖。

```bash
python3.12 -m venv "$VIDEO_RUNTIME/download-tools"
"$VIDEO_RUNTIME/download-tools/bin/python" -m pip install huggingface_hub
"$VIDEO_RUNTIME/download-tools/bin/hf" download Qwen/Qwen3-VL-8B-Instruct \
  --local-dir /mnt/video-models/Qwen3-VL-8B-Instruct
"$VIDEO_RUNTIME/download-tools/bin/hf" download Comfy-Org/MiniMax-H3 \
  diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors \
  text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
  vae/minimax_h3_video_vae_int8_convrot.safetensors \
  vae/minimax_h3_audio_vae_fp32.safetensors \
  --local-dir "$VIDEO_RUNTIME/ComfyUI/models"
```

若配置使用 ComfyUI 超分，另按 [ComfyUI 接入](comfyui.md) 准备 RealESRGAN 权重。将 ComfyUI 源码、两份 lock、两个 wheelhouse、模型目录带到离线节点；在线准备环境本身无需搬迁。

## 离线建环境与启动

GPU 驱动由节点环境提供，并须支持上述 CUDA 运行库。使用与在线准备相同的 Python 版本：

```bash
export VIDEO_RUNTIME=/mnt/video-runtime
python3.12 -m venv "$VIDEO_RUNTIME/ComfyUI/.venv"
"$VIDEO_RUNTIME/ComfyUI/.venv/bin/python" -m pip install --no-index \
  --find-links "$VIDEO_RUNTIME/wheels/comfyui" -r "$VIDEO_RUNTIME/comfyui.lock"
python3.12 -m venv "$VIDEO_RUNTIME/vllm"
"$VIDEO_RUNTIME/vllm/bin/python" -m pip install --no-index \
  --find-links "$VIDEO_RUNTIME/wheels/vllm" -r "$VIDEO_RUNTIME/vllm.lock"
```

从应用目录分别启动服务，每条命令占用一个终端或进程。下面示例将 ComfyUI 放在 GPU 0，Qwen 放在 GPU 7；启动脚本强制使用本地模型资源。

```bash
COMFYUI_DIR=/mnt/video-runtime/ComfyUI COMFY_GPUS=0 COMFY_PORT=8188 \
  bash deploy/start-comfyui.sh

PATH=/mnt/video-runtime/vllm/bin:$PATH \
  QWEN_MODEL_PATH=/mnt/video-models/Qwen3-VL-8B-Instruct REVIEW_GPUS=7 \
  FLASHINFER_WORKSPACE_BASE=/mnt/video-cache/vllm \
  bash deploy/start-vllm.sh
```

预编译 kernels 随 vLLM wheelhouse 安装；运行时编译缓存放在数据盘的 `FLASHINFER_WORKSPACE_BASE`。单卡 PRO6000 可先仅启用 ComfyUI，采用 CPU Review；8 卡 B200 可按 [配置文件](../configs/b200.yaml) 的端口分别启动多个 ComfyUI 实例，并为各实例指定不同 `COMFY_GPUS`、`COMFY_PORT`、`COMFY_INPUT_DIR`、`COMFY_OUTPUT_DIR`。

```bash
.venv/bin/videopipe run examples/product/package.json \
  --config configs/pro6000.yaml --output /mnt/video-runs/pro6000-example
```

真实生产默认保留人工审核，审核通过后使用 `videopipe approve <run_dir>` 交付。GPU 显存分配与模型画质须在目标节点实测；CPU 验收只证明应用和协议链路。
