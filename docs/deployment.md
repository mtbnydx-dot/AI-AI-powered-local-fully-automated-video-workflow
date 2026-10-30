<!--
<document version="0.3.0" updated_at="2026-10-30T00:00:00+08:00" keywords="离线部署,bundle,wheelhouse,ComfyUI,vLLM,B200,NVFP4" />
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

视觉审核使用 [NVIDIA `Qwen3.8-Flash-Next-NVFP4`](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4)。按其模型卡，vLLM 构建须包含提交 `d4d703caf908786416585ceb1f369e2e0363358b` 或兼容的后续实现；这里不再将旧 `0.32.0` 当作已验证基准。离线安装以实际 B200 部署验证过的固定 wheel 为输入，记录该 wheel 的版本、源提交、SHA256、Python/CUDA/驱动组合和启动参数，再保存对应依赖。仅版本号更新不代表已经通过本模型的验证。[vLLM 安装说明](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/)

执行前设置 `VLLM_WHEEL` 为该 Linux wheel 的绝对路径，`VLLM_TORCH_INDEX` 和 `VLLM_FLASHINFER_INDEX` 为该构建对应的 PyTorch、FlashInfer CUDA wheel 索引地址；不要沿用未核对的 CUDA 版本。以下命令保留离线 wheelhouse 流程，不会替你选择或验证模型兼容版本。

```bash
python3.12 -m venv "$VIDEO_RUNTIME/vllm-build"
: "${VLLM_WHEEL:?Set the absolute path of the fixed, compatible vLLM wheel}"
: "${VLLM_TORCH_INDEX:?Set the PyTorch index matching this vLLM build}"
: "${VLLM_FLASHINFER_INDEX:?Set the FlashInfer CUDA index matching this vLLM build}"
cp "$VLLM_WHEEL" "$VIDEO_RUNTIME/wheels/vllm/"
sha256sum "$VLLM_WHEEL" > "$VIDEO_RUNTIME/vllm-wheel.sha256"
"$VIDEO_RUNTIME/vllm-build/bin/python" -m pip install "$VLLM_WHEEL" \
  --extra-index-url "$VLLM_TORCH_INDEX" \
  --extra-index-url https://flashinfer.ai/whl \
  --extra-index-url "$VLLM_FLASHINFER_INDEX"
"$VIDEO_RUNTIME/vllm-build/bin/vllm" download-kernels
"$VIDEO_RUNTIME/vllm-build/bin/python" -m pip list --format=freeze > "$VIDEO_RUNTIME/vllm.lock"
"$VIDEO_RUNTIME/vllm-build/bin/python" -m pip wheel -r "$VIDEO_RUNTIME/vllm.lock" \
  --find-links "$VIDEO_RUNTIME/wheels/vllm" \
  --extra-index-url "$VLLM_TORCH_INDEX" \
  --extra-index-url https://flashinfer.ai/whl \
  --extra-index-url "$VLLM_FLASHINFER_INDEX" -w "$VIDEO_RUNTIME/wheels/vllm"
```

在线准备时预先下载 FlashInfer kernels，并将其依赖随 wheelhouse 固定。上述命令使用所选构建的 `vllm download-kernels`；若定制构建的安装方式不同，应按该构建的安装说明补齐同等离线资源，并在断网环境验证启动。

使用 Hugging Face CLI 在在线侧下载完整 NVIDIA Qwen 模型目录（包含 processor/tokenizer 等文件）；H3 下载四个指定文件到 ComfyUI 的模型目录。下载工具不属于生产应用依赖。正式交付还应在部署记录中固定模型仓库 revision。

```bash
python3.12 -m venv "$VIDEO_RUNTIME/download-tools"
"$VIDEO_RUNTIME/download-tools/bin/python" -m pip install huggingface_hub
"$VIDEO_RUNTIME/download-tools/bin/hf" download nvidia/Qwen3.8-Flash-Next-NVFP4 \
  --local-dir /mnt/video-models/Qwen3.8-Flash-Next-NVFP4
"$VIDEO_RUNTIME/download-tools/bin/hf" download Comfy-Org/MiniMax-H3 \
  diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors \
  text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
  vae/minimax_h3_video_vae_int8_convrot.safetensors \
  vae/minimax_h3_audio_vae_fp32.safetensors \
  --local-dir "$VIDEO_RUNTIME/ComfyUI/models"
```

若配置使用 ComfyUI 超分，另按 [ComfyUI 接入](comfyui.md) 准备 RealESRGAN 权重。将 ComfyUI 源码、两份 lock、两个 wheelhouse、模型目录及 vLLM wheel 校验值和部署记录带到离线节点；在线准备环境本身无需搬迁。

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
  QWEN_MODEL_PATH=/mnt/video-models/Qwen3.8-Flash-Next-NVFP4 \
  REVIEW_GPUS=7 REVIEW_TP=1 REVIEW_GPU_MEMORY=0.80 \
  REVIEW_CONTEXT=16384 REVIEW_MAX_SEQS=2 \
  FLASHINFER_WORKSPACE_BASE=/mnt/video-cache/vllm \
  bash deploy/start-vllm.sh
```

脚本固定 `--quantization modelopt`，通过官方 [`--engram-config '{"cpu_offload":true}'`](https://github.com/vllm-project/vllm/blob/main/vllm/config/engram.py) 将相应 embedding 放到 CPU，使用 TP1，不启用 MTP。0.80 显存比例、16K context 和并发 2 是本项目的初始值，尚未在本轮真实 B200 上验证；CPU offload 也需要足够主机内存。

GPU7 供 vLLM 常驻进程与可选 ASR 子进程共用。`configs/b200.yaml` 默认 `asr.enabled: false`；启用时须为应用环境单独准备并锁定 `videopipe[asr]` 的可选依赖 wheelhouse（CPU bundle 不包含它），以及完整本地 `large-v3` 模型，或将 `asr.model` 指向本地目录。配置采用 `gpu: 7`、`compute_type: int8_float16`。ASR 在成片拼接后运行一次，结束后退出；vLLM 仍常驻，二者显存需求仍会重叠。`REVIEW_GPU_MEMORY=0.80` 不是 ASR 的硬隔离或剩余显存保证。ASR 不可用只记日志，不阻断人工终审。

先用两路单镜头审核及启用 ASR 后的完整流程测量 GPU7 峰值显存、延迟和失败情况。稳定后如需提高吞吐，同时把 `REVIEW_MAX_SEQS` 与 `review.workers` 从 2 调到 4，再重复同样实测；不因启动成功就认定四路审核和 ASR 共存已满足容量要求。将最终参数与 wheel、模型 revision 一起写入部署记录。

预编译 kernels 随 vLLM wheelhouse 安装；运行时编译缓存放在数据盘的 `FLASHINFER_WORKSPACE_BASE`。单卡 PRO6000 可先仅启用 ComfyUI，采用 CPU Review；8 卡 B200 按 [配置文件](../configs/b200.yaml) 在 GPU0–6 启动七个 ComfyUI 实例，为各实例指定不同 `COMFY_GPUS`、`COMFY_PORT`、`COMFY_INPUT_DIR`、`COMFY_OUTPUT_DIR`，GPU7 留给上述 Qwen 和可选 ASR。

```bash
.venv/bin/videopipe run examples/product/package.json \
  --config configs/pro6000.yaml --output /mnt/video-runs/pro6000-example
```

真实生产默认保留人工审核，审核通过后使用 `videopipe approve <run_dir>` 交付。GPU 显存分配与模型画质须在目标节点实测；CPU 验收只证明应用和协议链路。
