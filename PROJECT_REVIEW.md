# 项目审查记录

审查日期：2026-06-22

## 结论

项目可以作为 GitHub 代码仓库上传。上传目录不包含模型文件、输出视频、缓存、日志或本机虚拟环境。

本轮重点审计结论：之前“生成不了”的核心风险不在 Wan2.2 模型本身，而在启动/发现链路。项目原来更偏向源码版 ComfyUI，遇到 ComfyUI Desktop 或父目录已有 ComfyUI runtime 时，前端可能判断为“未安装/不可启动”，导致一键配置下载了内容但没有正确加载到真实运行的 ComfyUI。现在前端会同时识别源码版 ComfyUI、ComfyUI Desktop bundled runtime、Desktop 可执行程序和正在运行的 ComfyUI active base。

## 已处理

- 新增前后端分离运行模式：`本机一体`、`服务端`、`客户端`。第一次运行可在环境页保存模式；服务端/本机一体会生成访问令牌，客户端会把安装、启动、自检、生成、诊断和媒体预览 API 自动转发到服务端。
- 启动器会根据已保存模式决定前端监听地址：未配置时保持本机安全默认，保存为 `服务端` 或 `本机一体` 后重启会监听 `0.0.0.0` 供局域网访问。
- 后端新增 `/api/client-config`、`/api/service-config` 和服务信息 payload；局域网非 GET 写操作在配置访问令牌后需要 `X-WAN22-Token` 或 `Authorization: Bearer`。
- 前端新增独立 `运行入口模式` 面板，显示本机地址、局域网地址、当前 API 地址、服务端地址和访问令牌；客户端模式下输出视频预览链接会自动改成服务端绝对地址。
- 前端本机输出路径已改为根据 `COMFY_BASE_DIR` / 环境侦测结果动态显示。
- 后端支持 `COMFY_BASE_DIR`，不再强依赖项目必须位于某个固定盘符路径。
- 统一入口 `START_WORKFLOW.py` 会把 `COMFY_BASE_DIR` 传给前端服务；只有用户显式设置 `COMFY_URL` 或启动器已发现 ComfyUI 在线时，才会固定传入 `COMFY_URL`，避免未发现 ComfyUI 时把前端锁死在 8000 端口而漏扫 8188。
- 前端第一步支持检测、安装/更新和启动 ComfyUI。
- 新增前端 `一键准备环境` 和后端 `/api/bootstrap/full-setup`：可按顺序安装/更新 ComfyUI、安装当前档位节点/模型，并在真实模式下尝试启动 ComfyUI。
- `启动 ComfyUI` 后台任务现在以 `/system_stats` 就绪为成功，不再等 ComfyUI 进程退出才结束，避免页面一直显示启动中。
- `一键准备环境` 失败或轮询断开后会重新加载环境状态，避免 `一键安装/修复缺失项` 按钮残留禁用导致小白只能刷新页面。
- 切换 `安装档位` 后，`一键准备环境` 会跟随新档位的磁盘预检刷新启用/禁用状态，避免从小档切到完整 A14B 档时沿用旧按钮状态。
- 如果已检测到 ComfyUI Desktop 或外部 ComfyUI 正在运行，`一键准备环境` 会跳过源码版 ComfyUI 安装/更新，只把模型和节点安装到当前 active base；active base 与前端 base 不一致时不会把本项目源码版 Python 误当成 Desktop 的节点 Python。
- ComfyUI / PyTorch 安装新增磁盘空间预检和 `--dry-run`，CUDA 默认建议预留 20GB。
- 环境侦测会读取运行中 ComfyUI 的 active base/input/output；默认启动未提供 `--base-directory` 时，会从 `/system_stats` 的 `main.py` 路径推断 base，避免 macOS / ComfyUI Desktop 下载目录和加载目录不一致。
- 加入 `加载诊断`：能识别文件已下载但 ComfyUI 模型列表未加载、自定义节点目录存在但节点未加载、文件下载不完整等情况。
- 一键安装脚本支持 `--comfy-python`，会自动安装自定义节点 `requirements.txt`。
- macOS/Windows 启动器优先使用项目目录 `.venv`，找不到依赖时输出明确安装命令。
- 新增 Windows `START_WORKFLOW.bat` 双击入口，自动用 `ExecutionPolicy Bypass` 调用 `.ps1`，降低小白第一次启动门槛。
- `START_WORKFLOW.py`、`.ps1`、`.bat`、`.command` 均支持 `--help`，只打印用法，不再误启动前端服务。
- 命令行脚本和后端自检子进程统一使用 UTF-8 输出/解码，修复 Windows 控制台和 `/api/self-test` 中中文诊断乱码或解码失败的问题。
- Windows 启动器会额外扫描 Python 3.10-3.12 常见安装目录，降低 winget 安装后 PATH 未刷新导致的启动失败。
- ComfyUI/PyTorch `auto` 后端会在 macOS 选择 MPS，检测到 NVIDIA 时选择 CUDA，否则选择 CPU，避免无 NVIDIA 的 Windows/Linux 机器误装 CUDA 大包。
- 未手动设置 `COMFY_URL` 时，启动器和前端会自动尝试本地 `8000` / `8188` 常见 ComfyUI 端口，兼容 ComfyUI Desktop 默认服务。
- 默认 `COMFY_BASE_DIR` 不再盲目使用项目父目录；只有父目录已像 ComfyUI base 时才沿用，否则回退项目目录。
- ComfyUI workflow 源码使用 `Wan2.2/...` 相对名，提交前会从 `/object_info` 替换成当前 ComfyUI 实际返回的模型选项。
- 媒体路径安全检查改为 `Path.relative_to()`，不再使用字符串前缀判断。
- ComfyUI 安装脚本阻断 Python 3.13+ / 3.9-，要求 Python 3.10-3.12。
- 模型选择会同步生成对应 ComfyUI API workflow，并在提交前预检。
- 48GB/64GB CUDA 的自动安装档位和默认正式片段已对齐为 Wan5B 保守主线；A14B 480P 保留为手动/专家尝试，避免小白自动安装 Wan5B 后默认点到缺失的 A14B。
- 新增 macOS Apple Silicon 独立路线：检测芯片、统一内存、MPS、ComfyUI venv torch 状态，并按 `mac-low`、`mac-balanced`、`mac-wan5b` 推荐模型。
- 新增 LTX 2B I2V/T2V workflow 生成器和 `workflows/mac/` 参考 workflow。
- 一键安装脚本新增 `--profile cuda-full/cuda-wan5b/mac-low/mac-balanced/mac-wan5b/post-only`，Mac 低档不会误拉 A14B，80GB 以下 CUDA 自动推荐 Wan5B 保守档。
- workflow 预检新增模型下拉列表校验和 Mac/MPS 风险提示；A14B 在 Mac 上默认拦截为专家风险。
- GitHub 上传目录加入 `.gitignore`，防止误传模型、输出、缓存和媒体文件。
- GitHub 上传目录加入 `requirements.txt` 和通用 README。
- GitHub 上传目录加入 `START_HERE.md`，给第一次使用者一个只包含启动、安装、排错和发布前检查的最短路径。
- 统一入口会自动创建前端 `.venv`、安装 `requirements.txt`，并用 `/api/health` 避免打开旧版前端。
- 生成任务只有 `success` 才会解锁下一步；失败、丢失或 ComfyUI 节点错误会停在当前步骤并显示日志。
- 一键安装计划会显示当前档位、缺失项数量和预计下载 GB；Mac 档位同时包含后处理小权重和节点。
- 安装档位下拉支持手动选择 `CUDA Wan5B 保守档`，安装计划刷新会忽略过期响应，避免快速切换档位时显示旧计划。
- 切换安装档位或重新环境侦测会取消 `一键准备环境` / `一键安装缺失项` 的二次确认状态，避免用户确认旧档位后切到新档位直接误安装。
- 一键安装计划新增磁盘空间预检，按真实剩余下载量和 `.part` 续传文件计算，空间不足时前端和后端都会阻断下载。
- ComfyUI 安装和模型/节点安装改为页面内二次确认，不再使用浏览器系统确认弹窗；自检会阻止系统确认弹窗回归。
- ffmpeg 检测改为真实执行探测，按 `FFMPEG_PATH`、系统 PATH、`imageio-ffmpeg` 兜底顺序显示来源、版本、滤镜状态和安装建议。
- 真实 `一键准备环境` 会在下载前用 HTTPS 检查本次档位实际需要的下载源；已就绪文件不会触发无关站点阻断，网络不可达时提前失败并给出代理/DNS/断点续传建议，HTTP 4xx 会按“站点可达”处理，避免 PyTorch 根地址 403 被误报为断网。
- 下载脚本改用 `.part` 临时文件，避免 ComfyUI 扫到半截模型；已有自定义节点会尝试 `git pull --ff-only` 并重新安装 requirements。
- 没有 Git 时，ComfyUI 与自定义节点安装会走 GitHub ZIP 兜底。
- ComfyUI 与自定义节点 ZIP 兜底下载会使用 `.part` 临时文件并自动重试，降低无 Git 环境下的网络抖动失败率。
- 大模型下载支持 `.part` 断点续传，前端缺失项会显示已下载体积、剩余体积和续传提示；完整 `.part` 会自动转正为模型文件，避免重复 Range 请求导致 416 或“已下载但没加载”。
- 默认 `COMFY_INSTALL_DIR` 如果被非 ComfyUI 目录占用，会自动退回项目内 `ComfyUI/`；显式设置环境变量时仍尊重用户配置。
- Windows 启动器用 winget 安装 Python 时加入协议确认参数并保留兼容兜底；macOS 启动器会检查常见 Homebrew/python.org Python 路径，并提示 `bash ./START_WORKFLOW.command` 兜底。
- Windows `START_WORKFLOW.ps1` 的运行提示改为 ASCII 文案，避免 Windows PowerShell 5.1 把无 BOM UTF-8 中文误读后出现字符串/大括号解析错误。
- Windows 没有 Python 且没有 winget 时，`START_WORKFLOW.ps1` 会提示打开官方 Python 下载页；`.bat` 和 `START_WORKFLOW.py` 也会打印同一兜底链接。
- 旧辅助入口 `scripts/start_beginner_frontend.ps1` 已并入统一入口，避免绕过 Python 安装、依赖修复、端口探测和 ComfyUI URL 自动发现。
- macOS 启动器在没有 Python 时会优先使用 Homebrew 安装 `python@3.12`；没有 Homebrew 但有 `curl` 时会询问是否安装 Homebrew，无法自动安装时会提示/打开官方 Python macOS 下载页。
- 前置条件医生里的 Windows `winget` 安装命令已统一加入 `--accept-package-agreements --accept-source-agreements`，和启动器行为保持一致。
- ComfyUI 与自定义节点 ZIP 兜底解压只会替换空目录；如果目标目录非空，会停止并提示用户换目录或清理，避免误删用户目录。
- 模型 URL、目标路径、大小和节点仓库以 `scripts/install_workflow_assets.py` 为单一来源，后端只补中文展示元数据。
- 新增 `scripts/self_check.py`，可在根工程或 GitHub 上传目录内直接验证代码语法、manifest、workflow 图、无 Git 兜底和发布包卫生。
- 新增 `scripts/release_smoke.py`，可把发布包复制到临时目录，验证启动器、安装器 dry-run、前端关键 API 和诊断接口，且不会下载模型或写入真实 ComfyUI 目录。
- 新增 `scripts/prerequisite_doctor.py` 和 `/api/prerequisites`，集中检测 Python、前端依赖、Git/ZIP 兜底、ffmpeg、磁盘、目录权限和 ComfyUI 安装目录状态。
- 新增 `/api/self-test` 和前端 `运行本机自检` 按钮，用户不用打开命令行也能运行前置条件检测和项目自检。
- 新增 `复制诊断信息` 和 `/api/diagnostics`，可把前端版本、ComfyUI 连接、硬件、安装档位、缺失项、磁盘预检和加载诊断汇总成 JSON；剪贴板被浏览器阻止时会回退显示在日志框。
- 新增 `/api/video-smoke-test` 和前端 `生成链路测试` 按钮：ComfyUI、模型和节点就绪后会提交一个最保守短视频任务，验证队列、模型名、节点和输出目录真实可用。
- `生成链路测试` 或正式生成遇到 ComfyUI 未连接时会返回结构化下一步：一键准备环境、启动 ComfyUI、再运行生成链路测试。
- 安装/启动任务失败时会按磁盘、网络、文件大小、pip、Git、Python/venv、ComfyUI 启动等类型给出下一步建议；诊断包会包含最近任务的失败分类和日志尾部。
- 新增 Hugging Face 下载源、pip 镜像源和 HTTP 代理设置：前端可保存镜像 endpoint、pip index 与代理地址，安装脚本支持 `WAN22_HF_ENDPOINT` / `HF_ENDPOINT` / `PIP_INDEX_URL`，ComfyUI 安装、PyTorch/pip 依赖安装、节点安装和模型下载子进程会继承 `HTTP_PROXY` / `HTTPS_PROXY`，网络预检会检查实际生效的下载源而不是固定官方域名；诊断包会脱敏代理账号密码。
- 下载源与代理区域新增 `测试下载源` 按钮：会先保存当前填写的 HF 镜像、pip 镜像和代理，再调用网络前置条件检测；不会安装 ComfyUI 或下载模型，只测试 GitHub、Hugging Face/镜像、PyPI 和 PyTorch 下载源，并刷新前置条件列表。
- 启动器、ComfyUI 安装器和自定义节点依赖安装器均新增 pip 兜底：先检测 pip，缺失时自动 `ensurepip --upgrade`，pip 安装失败会重试，降低空白机器或网络抖动导致的首次启动失败。
- 前置条件医生优先检查项目 `.venv` 的前端依赖；只有 `.venv` 不存在或不可用时才检查当前 Python，避免 Codex/系统 Python 与项目 Python 不一致时误报依赖缺失。
- 前置条件医生的手动修复命令已补齐 `ensurepip`、`pip install -U pip` 和 `pip install -r requirements.txt` 三步，与自动启动器行为保持一致。
- ComfyUI Desktop/runtime 检测已补齐：当前机器这类结构 `ComfyUI/resources/ComfyUI/main.py` + base `.venv` 会被识别为可启动 runtime，`启动 ComfyUI` 和 `一键准备环境` 不再要求必须存在源码版 `<COMFY_INSTALL_DIR>/main.py`。
- 环境页会显示 `ComfyUI runtime`，包括 runtime 类型、bundled main、Desktop 可执行程序和实际 Python；小白能看出现在到底复用的是源码版、Desktop runtime 还是外部 ComfyUI。
- 前置条件医生已识别 ComfyUI Desktop/runtime 目录，不会再把 `ComfyUI/resources/ComfyUI/main.py` 结构误判为“非源码目录占用”并阻断第一步环境检测。
- 未连接 ComfyUI 时，自定义节点目录存在不会再被误报成“节点未加载失败”；会显示为待连接/待验证，避免用户在服务没启动时被错误诊断带偏。
- 上传关键帧和复制媒体到 ComfyUI input 时加入唯一文件名，避免同名文件互相覆盖导致后续步骤拿到旧图或旧视频。
- 新增自检覆盖 Desktop runtime 分支、parent ComfyUI base 默认发现、一键准备复用正确 Python，以及启动按钮状态。

## 未包含在 GitHub 上传目录

- `__pycache__/`
- `*.pyc`
- 前端运行日志
- 本机绝对路径指南
- 模型文件
- ComfyUI `models/`、`input/`、`output/`、`temp/`、`custom_nodes/`、`user/`

## 运行风险

- 当前根目录的 `.git` 不是可用 Git 仓库；真正上传 GitHub 时应使用 `github_upload/wan22-local-video-workflow` 作为新仓库根目录重新 `git init`，不要直接上传根目录。
- ComfyUI Desktop 的 Python 环境不一定能被外部脚本自动定位；如果 `--comfy-python` 不可用，仍可能需要在 Desktop 的节点管理器里安装依赖。
- A14B 720P 主要依赖单卡显存，双 48GB 默认不等价于单 96GB。
- macOS Apple Silicon 已有 LTX 默认视频路线，但 Wan5B 仍是高内存实验档；A14B 在 MPS 上不作为默认推荐。
- LTX 依赖 ComfyUI 新版原生节点；如果节点缺失，需要更新 ComfyUI 或使用 ComfyUI Desktop 模板/管理器修复。
- ffmpeg 闪烁修复需要本机 ffmpeg 包含 `deflicker` 和 `hqdn3d` 滤镜。
- 从网页/压缩包获取源码时，macOS `.command` 可能丢失执行权限；README 已要求运行 `chmod +x ./START_WORKFLOW.command`。
- 当前验证覆盖启动器、安装 dry-run、前端 API、自检、发布包卫生、ComfyUI Desktop runtime 发现和本机真实短视频生成；尚未在一台完全空白机器上完成真实 ComfyUI 安装和完整大模型下载，因此首次空白机仍需按目标硬件做最终验收。
- 当前机器网络医生显示 GitHub、PyPI、PyTorch 下载源可达，但 Hugging Face 不可达；真实模型下载前需要在前端 `下载源与代理` 里配置可用 HF 镜像或代理，或等网络恢复。

## 验证项

- Python 语法检查
- 前端 JS 语法检查
- `/api/environment`
- `/api/client-config`、`/api/service-config`、`/api/health` 服务模式接口
- `/api/validate`
- `/api/workflow` 保守档 workflow 预检
- `/api/workflow` Mac LTX workflow 预检
- `/api/diagnostics` 诊断汇总接口
- `/api/self-test` 前端本机自检任务
- 安装失败分类和最近任务诊断摘要
- `cuda-wan5b` 安装档位计划和 UI 切换
- Mac 分档推荐模拟：M3 Max 128GB -> `mac-wan5b`，16GB Apple Silicon -> `mac-low`
- 硬件矩阵自检覆盖 96GB CUDA、48GB CUDA、16GB Apple Silicon、128GB Apple Silicon 和 CPU-only，验证推荐模型与安装档位不串档
- `/api/install` 无缺失项时干净跳过
- `install_workflow_assets.py --dry-run` 可在不下载文件的情况下输出档位和磁盘空间计划
- 大文件下载续传测试会制造 `.part` 半截文件并验证 Range 续传后的完整内容，也会验证完整 `.part` 不联网即可转正
- `install_comfyui.py --dry-run` 可在不安装 ComfyUI/PyTorch 的情况下输出安装磁盘计划
- `START_WORKFLOW.bat --check`、`START_WORKFLOW.ps1 --check`、`START_WORKFLOW.py --check` 可在不启动前端的情况下验证入口
- `scripts/start_beginner_frontend.ps1 --check` 会转发到统一入口，确认旧辅助入口不会绕过主启动链路
- `python scripts/prerequisite_doctor.py --json` 可单独检查第一次运行的本机前置条件
- `python scripts/release_smoke.py --json` 可在临时目录模拟发布包首次启动、Windows `.bat/.ps1` 多入口、旧辅助入口和关键 API，并验证前置条件 API 暴露 Python 官方下载兜底、ComfyUI 未连接时的生成链路恢复建议
- release smoke 覆盖 `START_WORKFLOW.py/ps1/bat --help`，确认查看帮助不会误启动服务
- release smoke 覆盖 `/api/bootstrap/full-setup` dry-run，确认一键准备会顺序调用 ComfyUI 安装器和资源安装器
- release smoke 覆盖 `/api/self-test`，确认前端按钮触发的本机自检能解析 UTF-8 诊断输出
- 自检覆盖 pip/venv 兜底：前端依赖、ComfyUI 依赖和自定义节点 requirements 的 pip 安装均会在瞬时失败后重试，并保留 `ensurepip` 修复路径。
- 自检覆盖下载源测试入口，确认环境页暴露按钮并调用 `/api/prerequisites?network=true`，用户可在真实安装前验证网络源。
- 自检覆盖服务模式：未配置默认 `本机一体`、服务端/双模式自动生成访问令牌、局域网写操作必须带 token、客户端模式保持本地监听并转发远程 API。
- 浏览器实测服务模式面板：首次未保存时只显示本机/API 状态，局域网地址提示“保存并重启后显示”；切换客户端后服务端地址输入启用，控制台无错误，模式卡在 1280 宽视口无明显重叠。
- 自检覆盖 running ComfyUI 分支，确认一键准备会跳过源码安装且不会向外部/ Desktop 环境传错 `--comfy-python`
- release smoke 会逐个检查 `post-only`、`cuda-wan5b`、`cuda-full`、`mac-low`、`mac-balanced`、`mac-wan5b` 安装档位，确认低配/Mac 档不会误拉 A14B
- 本轮真实启动过 `START_WORKFLOW.py`，前端返回 `/`、`/static/app.js`、`/static/styles.css`、`/api/bootstrap`、`/api/environment`、`/api/status`；本机 Python 环境未安装 Playwright，因此没有做控制台错误抓取
- 本轮在当前机器识别到 ComfyUI Desktop runtime：`ComfyUI/resources/ComfyUI/main.py` + base `.venv`，并成功用该 runtime 启动 ComfyUI 服务。
- 本轮 `/object_info` 已确认 `WanImageToVideo`、`Wan22ImageToVideoLatent`、`UNETLoader`、`CLIPLoader`、`VAELoader`、`KSamplerAdvanced`、`VHS_VideoCombine`、`RIFE VFI`、`UpscaleModelLoader` 和 `ImageUpscaleWithModel` 可见。
- 本轮真实执行 `生成链路测试`，使用 Wan2.2 TI2V-5B 480P 保守短视频参数成功出片；输出保存到 ComfyUI base 的 `output/wan22_frontend/`。
- `/api/video-smoke-test` 在 ComfyUI 未连接时返回 503、明确原因和下一步动作；真实生成路径仍需要模型和 ComfyUI 就绪后验收
- workflow JSON 可解析，且不再包含 `Wan2.2\\...` 模型路径
- 发布包卫生扫描会拦截模型、视频、缓存、日志和 `.part` 半截下载文件
- 发布包卫生扫描额外拦截 `.pt`、`.onnx`、`.bin`、`.zip`、`.tar*`、`.7z`、`.rar` 等常见模型/下载残留
- `python scripts/self_check.py --json` 在根工程和 GitHub 上传目录均通过
- `python scripts/prerequisite_doctor.py --network --json` 可确认项目 `.venv` 依赖状态、Git/ffmpeg/磁盘/权限和下载源连通性；本轮结果仅 Hugging Face 为 warning。
