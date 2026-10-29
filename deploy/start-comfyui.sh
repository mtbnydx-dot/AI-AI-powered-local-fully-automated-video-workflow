#!/usr/bin/env bash
set -euo pipefail

: "${COMFYUI_DIR:?Set COMFYUI_DIR to the prepared external ComfyUI checkout}"
[[ -f "$COMFYUI_DIR/main.py" ]] || { echo "ComfyUI main.py not found in: $COMFYUI_DIR" >&2; exit 1; }

export CUDA_VISIBLE_DEVICES="${COMFY_GPUS:-0}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

comfy_python="${COMFYUI_PYTHON:-$COMFYUI_DIR/.venv/bin/python}"
comfy_input="${COMFY_INPUT_DIR:-$COMFYUI_DIR/input}"
comfy_output="${COMFY_OUTPUT_DIR:-$COMFYUI_DIR/output}"
mkdir -p "$comfy_input" "$comfy_output"

comfy_args=(
    --listen "${COMFY_HOST:-127.0.0.1}"
    --port "${COMFY_PORT:-8188}"
    --input-directory "$comfy_input"
    --output-directory "$comfy_output"
    --offline
    --disable-auto-launch
)
if [[ -n "${COMFY_MODEL_PATHS:-}" ]]; then
    comfy_args+=(--extra-model-paths-config "$COMFY_MODEL_PATHS")
fi

cd "$COMFYUI_DIR"
exec "$comfy_python" main.py "${comfy_args[@]}" "$@"
