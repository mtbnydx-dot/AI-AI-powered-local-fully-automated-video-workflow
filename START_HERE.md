# Start Here

这是给第一次使用者的最短路径。模型文件不在仓库里，前端会按你的电脑配置提示安装。

## 1. 启动

Windows：双击 `START_WORKFLOW.bat`。如果没有 Python，启动器会优先询问是否用 winget 安装；没有 winget 时会提示官方 Python 下载页。

macOS / Linux：

```bash
chmod +x ./START_WORKFLOW.command
./START_WORKFLOW.command
```

如果 macOS 下载后执行权限丢失，也可以直接运行 `bash ./START_WORKFLOW.command`。macOS 没有 Python 时，启动器会优先尝试 Homebrew 安装；没有 Homebrew 时会询问是否安装 Homebrew，或打开官方 Python 下载页。

如果你更习惯命令行：

```bash
python START_WORKFLOW.py
```

启动后浏览器会打开本地前端。先停在第 1 步 `环境侦测`。

第一次打开前端时，先在 `运行入口模式` 里选这台机器的角色：

- `本机一体`：前端、安装器、ComfyUI 和生成任务都在这台机器上，最适合首次开箱。
- `服务端`：这台机器负责安装和生成，局域网里的其他电脑可以打开它的前端或作为客户端连接。
- `客户端`：这台机器只显示前端，把安装、自检、生成和诊断 API 提交给另一台服务端。

选择 `服务端` 或 `本机一体` 后会自动生成访问令牌；局域网里的写操作需要这个令牌。切换到 `服务端` 或 `本机一体` 后，请重启 `start.bat` / `START_WORKFLOW.bat`，让前端监听局域网地址。服务端页面会显示本机地址、局域网地址和当前 API 地址。

启动器会先尝试修复 venv 里的 pip（`ensurepip`），再安装前端依赖；pip 下载失败会自动重试。若多次重试后仍卡在“安装前端依赖”，通常是 PyPI 或代理网络问题。现在启动器会直接在窗口里询问是否填写 pip 镜像或 HTTP 代理，保存后会自动重试一次，不需要先打开前端。命令行备用方式如下：

```bash
python START_WORKFLOW.py --set-pip-index https://pypi.org/simple
python START_WORKFLOW.py --set-proxy http://127.0.0.1:7890
python START_WORKFLOW.py --show-download-settings
```

Windows 也可以把上面的 `python START_WORKFLOW.py` 换成 `START_WORKFLOW.bat`；macOS 可以换成 `./START_WORKFLOW.command`。需要清空这些本地设置时运行 `python START_WORKFLOW.py --clear-download-settings`。

## 2. 在前端里做

1. 先看 `环境侦测` 给出的推荐档位。
2. 如果 Hugging Face、PyPI 或 PyTorch 下载不通，可以在 `下载源与代理` 里填 Hugging Face 镜像地址、pip 镜像地址，或填本机 HTTP 代理地址；留空就是官方源且不使用额外代理。填完可直接点 `测试下载源`，系统会先保存当前设置，再确认这些下载站点是否可达。
3. 不想折腾参数时，直接点击 `一键准备环境`。ComfyUI 未连接时会安装/更新源码版 ComfyUI；已经连接 ComfyUI Desktop 或其他 ComfyUI 时，会跳过源码安装，把节点和模型安装到当前 active base。保存过的网络设置会自动传给 ComfyUI 安装、PyTorch/pip 依赖安装、节点安装和模型下载；pip 缺失会自动 `ensurepip`，pip 下载失败会重试。
4. 如果你电脑里已有 ComfyUI Desktop，但它还没启动，环境页会显示 `ComfyUI runtime`。只要检测到 Desktop bundled runtime 和可用 Python，`启动 ComfyUI` 会直接复用它，不会要求你重新装一份源码版。
5. 如果你想手动控制，也可以分别点击 `安装/更新 ComfyUI`、`启动 ComfyUI`、选择 `安装档位`、再点 `一键安装/修复缺失项`。
6. 安装节点或模型后，如果页面提示需要重启 ComfyUI，就重新启动 ComfyUI，再点 `重新侦测`。
7. 点击 `生成链路测试`，确认 ComfyUI 队列、模型、节点和输出目录真的能跑通一个短视频。
8. 上传关键帧，按页面步骤继续生成、修复、插帧、超分。

如果你已经把模型或权重文件放在项目上级目录的 ComfyUI `models/` 里，安装器会自动识别并复用同名且大小匹配的文件；前端会显示 `本地缓存可用`。高级用法可以设置 `WAN22_LOCAL_ASSET_DIRS`，用系统路径分隔符放多个缓存目录，安装时会先找本地文件，找不到才下载。

推荐档位：

- 80GB+ NVIDIA：`CUDA 完整 Wan2.2 档`
- 24GB-64GB NVIDIA：`CUDA Wan5B 保守档`
- Apple Silicon：按前端推荐的 Mac 档位
- 只做后期：`仅后期工具`

## 3. 出问题时

先回到第 1 步 `环境侦测`。

如果需要别人帮你排查，点击右侧 `下载诊断包`，把下载的 JSON 文件发给维护者；也可以点 `复制诊断信息` 直接复制到聊天窗口。诊断内容会脱敏代理账号密码。

`运行本机自检` 不会下载模型，也不会真实生成；`一键准备环境` 会真实安装 ComfyUI、节点和模型；`生成链路测试` 会在模型和 ComfyUI 就绪后提交一个最保守的短视频任务，更适合验证“已经可以生成了”。

## 4. 上传 GitHub 前检查

这些命令不会下载模型，也不会启动真实生成：

```bash
python scripts/prerequisite_doctor.py --json
python scripts/self_check.py --json
python scripts/release_smoke.py --json
python scripts/clean_bootstrap_smoke.py --json
python START_WORKFLOW.py --check
```

`clean_bootstrap_smoke.py` 会复制发布包到临时目录，真实创建一个全新的 `.venv` 并启动前端；它需要能访问 PyPI 或你配置的 pip 镜像，但不会下载视频模型。

Windows 还可以检查双击入口：

```powershell
.\START_WORKFLOW.bat --check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\START_WORKFLOW.ps1 --check
```

发布包目录是 `github_upload/wan22-local-video-workflow`；如果你已经打开的是这个目录，它本身就是发布包根目录。
