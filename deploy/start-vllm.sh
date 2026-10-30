#!/usr/bin/env bash
set -euo pipefail

# Preload nvidia/Qwen3.8-Flash-Next-NVFP4 and its processor/tokenizer into this local directory.
# Model card: https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4
# vLLM must include d4d703caf908786416585ceb1f369e2e0363358b or a compatible later version.
# Engram CPU embedding offload: https://github.com/vllm-project/vllm/blob/main/vllm/config/engram.py
# GPU7 is shared with ASR. 0.80 VRAM / 16K context / 6 sequences are initial settings, not capacity guarantees.
QWEN_MODEL_PATH="${QWEN_MODEL_PATH:-/mnt/video-models/Qwen3.8-Flash-Next-NVFP4}"
[[ -d "$QWEN_MODEL_PATH" ]] || { echo "Model directory does not exist: $QWEN_MODEL_PATH" >&2; exit 1; }

export CUDA_VISIBLE_DEVICES="${REVIEW_GPUS:-7}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export VLLM_NO_USAGE_STATS=1

exec vllm serve "$QWEN_MODEL_PATH" \
    --served-model-name "${REVIEW_MODEL_NAME:-qwen-review}" \
    --host "${REVIEW_HOST:-127.0.0.1}" \
    --port "${REVIEW_PORT:-8000}" \
    --tensor-parallel-size "${REVIEW_TP:-1}" \
    --quantization modelopt \
    --engram-config '{"cpu_offload":true}' \
    --reasoning-parser qwen3 \
    --trust-remote-code \
    --max-model-len "${REVIEW_CONTEXT:-16384}" \
    --max-num-seqs "${REVIEW_MAX_SEQS:-6}" \
    --gpu-memory-utilization "${REVIEW_GPU_MEMORY:-0.80}" \
    --limit-mm-per-prompt "{\"image\":${REVIEW_MAX_IMAGES:-64},\"video\":0}" \
    "$@"
