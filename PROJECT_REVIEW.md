# 项目审查记录

审查日期：2026-06-13

## 结论

项目可以作为 GitHub 代码仓库上传。上传目录不包含模型文件、输出视频、缓存、日志或本机虚拟环境。

## 已处理

- 前端本机输出路径已改为根据 `COMFY_BASE_DIR` / 环境侦测结果动态显示。
- 后端支持 `COMFY_BASE_DIR`，不再强依赖项目必须位于某个固定盘符路径。
- 统一入口 `START_WORKFLOW.py` 会把 `COMFY_BASE_DIR` 和 `COMFY_URL` 传给前端服务。
- 模型选择会同步生成对应 ComfyUI API workflow，并在提交前预检。
- GitHub 上传目录加入 `.gitignore`，防止误传模型、输出、缓存和媒体文件。
- GitHub 上传目录加入 `requirements.txt` 和通用 README。

## 未包含在 GitHub 上传目录

- `__pycache__/`
- `*.pyc`
- 前端运行日志
- 本机绝对路径指南
- 模型文件
- ComfyUI `models/`、`input/`、`output/`、`temp/`、`custom_nodes/`、`user/`

## 运行风险

- ComfyUI 自定义节点克隆后，某些节点的 Python 依赖可能仍需在 ComfyUI 使用的 Python 环境中安装。
- A14B 720P 主要依赖单卡显存，双 48GB 默认不等价于单 96GB。
- macOS 可以运行入口和前端，但 Wan 视频模型在 MPS 上的兼容性和速度取决于 ComfyUI、PyTorch 和节点版本。
- ffmpeg 闪烁修复需要本机 ffmpeg 包含 `deflicker` 和 `hqdn3d` 滤镜。

## 验证项

- Python 语法检查
- 前端 JS 语法检查
- `/api/environment`
- `/api/validate`
- `/api/workflow` 保守档 workflow 预检
