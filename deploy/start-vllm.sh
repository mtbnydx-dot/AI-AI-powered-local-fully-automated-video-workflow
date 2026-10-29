#!/usr/bin/env bash
set -euo pipefail

: "${QWEN_MODEL_PATH:?Set QWEN_MODEL_PATH to the mounted local Qwen3-VL model directory}"
[[ -d "$QWEN_MODEL_PATH" ]] || { echo "Model directory does not exist: $QWEN_MODEL_PATH" >&2; exit 1; }

export CUDA_VISIBLE_DEVICES="${REVIEW_GPUS:-0}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export VLLM_NO_USAGE_STATS=1

exec vllm serve "$QWEN_MODEL_PATH" \
    --served-model-name "${REVIEW_MODEL_NAME:-qwen-review}" \
    --host "${REVIEW_HOST:-127.0.0.1}" \
    --port "${REVIEW_PORT:-8000}" \
    --tensor-parallel-size "${REVIEW_TP:-1}" \
    --max-model-len "${REVIEW_CONTEXT:-32768}" \
    --max-num-seqs "${REVIEW_MAX_SEQS:-2}" \
    --gpu-memory-utilization "${REVIEW_GPU_MEMORY:-0.85}" \
    --limit-mm-per-prompt "{\"image\":${REVIEW_MAX_IMAGES:-64},\"video\":0}" \
    "$@"
