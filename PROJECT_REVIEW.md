# 项目审查记录

审查日期：2026-06-13

## 结论

项目可以作为 GitHub 代码仓库上传。上传目录不包含模型文件、输出视频、缓存、日志或本机虚拟环境。

## 已处理

- 前端本机输出路径已改为根据 `COMFY_BASE_DIR` / 环境侦测结果动态显示。
- 后端支持 `COMFY_BASE_DIR`，不再强依赖项目必须位于某个固定盘符路径。
- 统一入口 `START_WORKFLOW.py` 会把 `COMFY_BASE_DIR` 和 `COMFY_URL` 传给前端服务。
- 前端第一步支持检测、安装/更新和启动 ComfyUI。
- 环境侦测会读取运行中 ComfyUI 的 active base/input/output，避免 macOS / ComfyUI Desktop 下载目录和加载目录不一致。
- 加入 `加载诊断`：能识别文件已下载但 ComfyUI 模型列表未加载、自定义节点目录存在但节点未加载、文件下载不完整等情况。
- 一键安装脚本支持 `--comfy-python`，会自动安装自定义节点 `requirements.txt`。
- macOS/Windows 启动器优先使用项目目录 `.venv`，找不到依赖时输出明确安装命令。
- 默认 `COMFY_BASE_DIR` 不再盲目使用项目父目录；只有父目录已像 ComfyUI base 时才沿用，否则回退项目目录。
- ComfyUI workflow 源码使用 `Wan2.2/...` 相对名，提交前会从 `/object_info` 替换成当前 ComfyUI 实际返回的模型选项。
- 媒体路径安全检查改为 `Path.relative_to()`，不再使用字符串前缀判断。
- ComfyUI 安装脚本阻断 Python 3.13+ / 3.9-，要求 Python 3.10-3.12。
- 模型选择会同步生成对应 ComfyUI API workflow，并在提交前预检。
- 新增 macOS Apple Silicon 独立路线：检测芯片、统一内存、MPS、ComfyUI venv torch 状态，并按 `mac-low`、`mac-balanced`、`mac-wan5b` 推荐模型。
- 新增 LTX 2B I2V/T2V workflow 生成器和 `workflows/mac/` 参考 workflow。
- 一键安装脚本新增 `--profile cuda-full/mac-low/mac-balanced/mac-wan5b/post-only`，Mac 低档不会误拉 A14B。
- workflow 预检新增模型下拉列表校验和 Mac/MPS 风险提示；A14B 在 Mac 上默认拦截为专家风险。
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

- ComfyUI Desktop 的 Python 环境不一定能被外部脚本自动定位；如果 `--comfy-python` 不可用，仍可能需要在 Desktop 的节点管理器里安装依赖。
- A14B 720P 主要依赖单卡显存，双 48GB 默认不等价于单 96GB。
- macOS Apple Silicon 已有 LTX 默认视频路线，但 Wan5B 仍是高内存实验档；A14B 在 MPS 上不作为默认推荐。
- LTX 依赖 ComfyUI 新版原生节点；如果节点缺失，需要更新 ComfyUI 或使用 ComfyUI Desktop 模板/管理器修复。
- ffmpeg 闪烁修复需要本机 ffmpeg 包含 `deflicker` 和 `hqdn3d` 滤镜。
- 从网页/压缩包获取源码时，macOS `.command` 可能丢失执行权限；README 已要求运行 `chmod +x ./START_WORKFLOW.command`。

## 验证项

- Python 语法检查
- 前端 JS 语法检查
- `/api/environment`
- `/api/validate`
- `/api/workflow` 保守档 workflow 预检
- `/api/workflow` Mac LTX workflow 预检
- Mac 分档推荐模拟：M3 Max 128GB -> `mac-wan5b`，16GB Apple Silicon -> `mac-low`
- `/api/install` 无缺失项时干净跳过
- 浏览器加载环境页、诊断区渲染、控制台无前端错误
- workflow JSON 可解析，且不再包含 `Wan2.2\\...` 模型路径
