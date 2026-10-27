# 本地 AI 视频工作台小白使用指南

旧名 Wan22 工作流。

## 启动

Windows 推荐直接双击：

```text
START_WORKFLOW.bat
```

如果你习惯 PowerShell，也可以运行统一入口：

```powershell
.\START_WORKFLOW.ps1
```

macOS 迁移后用：

```bash
cd "/path/to/project-folder"
chmod +x START_WORKFLOW.command
./START_WORKFLOW.command
```

统一入口会自动创建项目 `.venv`、安装前端依赖、检查 ComfyUI 是否连得上，然后启动小白前端并打开 `http://127.0.0.1:7860`。Windows 的 `.bat` 会自动用 `ExecutionPolicy Bypass` 调用 `.ps1`，避免第一次双击被 PowerShell 策略拦住。如果 Windows 没有 Python 但有 winget，启动器会询问是否安装 Python 3.12，并且会额外检查 Python 常见安装目录，减少安装后 PATH 没刷新的失败。macOS 没有 Python 但有 Homebrew 时，启动器会询问是否安装 Python 3.12。安装前端依赖前会自动运行 `ensurepip` 修复缺失的 pip，pip 下载失败会重试；如果重试后仍失败，启动器会在窗口里询问是否填写 pip 镜像或 HTTP 代理，保存后自动再试一次。ComfyUI 还没启动时前端仍然会打开，「环境与安装」页会提示缺什么。

如果第一次启动停在“安装前端依赖”，说明浏览器前端还没起来；启动器已经尝试过 `ensurepip` 和 pip 重试，多半是 pip 网络或代理问题。优先按启动窗口提示填写 pip 镜像或代理；命令行备用方式如下：

```powershell
python START_WORKFLOW.py --set-pip-index https://pypi.org/simple
python START_WORKFLOW.py --set-proxy http://127.0.0.1:7890
python START_WORKFLOW.py --show-download-settings
```

Windows 可把 `python START_WORKFLOW.py` 换成 `.\START_WORKFLOW.bat`，macOS 可换成 `./START_WORKFLOW.command`。这些设置写入本地 `.local_ai_workbench_config.json`，不会进入 GitHub 发布包；旧版 `.wan22_workflow_config.json` 会自动兼容读取。清空时运行 `python START_WORKFLOW.py --clear-download-settings`。

如果没有手动设置 `COMFY_URL`，启动器和前端默认使用 ComfyUI 端口 `8188`，并自动探测 `8000`/`8188`。这能兼容前端自己启动的 ComfyUI，也能兼容 ComfyUI Desktop 或默认端口服务。你手动设置了 `COMFY_URL` 时，系统会严格使用你的配置。

旧入口只是兼容包装，也会转到统一入口：

```powershell
.\scripts\start_beginner_frontend.ps1
```

## 前后端分离和局域网模式

第一次打开「环境与安装」标签页，页面顶部会出现 `运行入口模式`。这个面板决定当前机器扮演什么角色：

- `本机一体`：默认小白模式。前端、安装器、ComfyUI 启动、模型下载和生成任务都在这台机器上。
- `服务端`：这台机器作为局域网生成服务器。保存后重启 `start.bat` 或 `START_WORKFLOW.bat`，前端会监听 `0.0.0.0`，页面会显示局域网地址。
- `客户端`：这台机器只开控制面板。填写服务端地址后，所有 `/api` 请求会自动转发到服务端，包括一键安装、启动 ComfyUI、自检、生成、诊断和输出预览。

服务端/本机一体模式会自动生成 `访问令牌`。局域网客户端执行写操作时需要填同一个令牌；本机浏览器操作不会因为令牌丢失而卡住。客户端模式下，右侧视频预览和输出链接会自动使用服务端地址，不需要手动改链接。

推荐使用方式：

1. 生成机器上先选择 `服务端` 或 `本机一体`，保存后重启入口。
2. 在服务端页面复制 `局域网地址` 和 `访问令牌`。
3. 其他电脑启动同一个前端，选择 `客户端`，填入服务端地址和令牌。
4. 点击 `刷新连接信息`，确认服务端和 ComfyUI 状态后再操作一键安装或生成。

## 一键自检

不下载模型、不启动真实生成，只检查代码、manifest、工作流图、无 Git 兜底和发布包卫生：

```powershell
python scripts/self_check.py
```

GitHub 上传目录里也可以直接运行同一条命令。

如果要模拟“别人刚下载 GitHub 包”的启动链路，可以运行：

```powershell
python scripts/release_smoke.py --json
```

它会把发布包复制到临时目录，检查启动器、安装器 dry-run、前端关键 API 和诊断接口；不会下载模型，也不会写入真实 ComfyUI 目录。

如果要进一步验证“本地完全没有项目 `.venv`”时也能首启，可以运行：

```powershell
python scripts/clean_bootstrap_smoke.py --json
```

它会复制发布包到临时目录，真实创建一个全新的 `.venv`、安装前端依赖并启动前端；需要 PyPI 或 pip 镜像可达，但不会下载视频模型。

## 按任务使用

首次使用先到「环境与模型」完成准备。日常按目的选入口，不必从头跑完整流程：

1. `图片创作`：文字生图或编辑已有图片。结果可直接送入视频生成，无需再次确认关键帧。
2. `视频生成`：选择模型，填写提示词；需要首帧、尾帧或参考素材的模型会显示对应输入。常用参数只展示主要设置，专业设置在「全部参数」中。
3. `视频处理`：上传已有片段或从作品中选择，直接进行修复与调色、流畅度增强、清晰度增强或拼接。它们是独立工具，不是必经步骤。

结果完成后直接下载使用，或在「继续处理」中选择工具。该工具会使用刚刚选择的结果，允许超分后再调色，也允许跳过插帧直接拼接。顶部「当前视频素材」始终用于核对输入。需要连续自动后期时在视频生成页明确勾选；手动处理不会沿用生成页的自动勾选。刷新会恢复入口、视频提示词、项目及参数；本机上传的文件仍要重新选择。

## 文生图工作台

顶部切换到 `图片创作` 后，模型下拉按 Qwen-Image、HiDream-O1-Image、FLUX.2、Z-Image 和 ERNIE-Image 等家族分组，具体档位以当前目录为准；选择后会同步显示量化、发布时间、显存建议、能力、官方工作流和当前缺失项。第一次使用先选模型与下载路径，再按缺失项安装；新节点安装完成后需在确认没有任务与资源冲突时重启 ComfyUI，再复检。

Qwen 与 Z-Image 有单独的社区成人内容 LoRA 安装按钮，安装完成后对应开关才会启用。HiDream、ERNIE 暂无已验证专用方案，FLUX.2 不集成许可不允许的绕过方案，页面会直接标注。所有成人内容模式只允许明确成年人内容。

- `中文直输`：不改写原文，适合已经写清楚的提示词或专有名词较多的场景。
- `结构化增强`：默认选项，保留中文语义并补齐主体、环境、构图、光线和质感约束。
- `中英混合增强`：中文结构化之后只补少量英文质量词，适合需要更强视觉风格时尝试。
- `画面文字`：单独填写海报标题、包装文字或招牌，系统会加入逐字呈现、不得翻译和不得增删的约束。
- `Lightning 快速档`：固定 4 步、CFG 1，适合试构图。
- `质量档`：标准 50 步、CFG 4，适合定稿。

结果可以逐张下载、批量下载，或点击 `送入视频关键帧` 直接切回视频工作流。视频关键帧仍是可选项；没有关键帧时使用文生视频链路，不会塞入内置示意图。

## 推荐顺序

1. 先看「环境与安装」，确认这台机器适合跑哪一档模型。
2. 用生图模型先做关键帧，或直接上传外部关键帧。
3. 在模型控制台选择目标模型，使用同步推荐的草稿档快速试动作。
4. 满意后使用同一目标模型的正式片段档生成。
5. 有闪烁时先做 `闪烁修复`，再插帧和超分。
6. 在第 6 步拼接里排序并合成多个 3 到 6 秒镜头，再导入剪辑软件完成声音、字幕和调色。

## 环境侦测和一键安装

第一步会显示：

- 当前系统：Windows / macOS、Python 版本。
- GPU 和显存：会区分单卡显存和多卡总显存。
- ComfyUI 状态：版本、队列、是否连上默认 `8188`（自动探测 `8000`/`8188`）。
- ComfyUI runtime：会显示当前可启动的是源码版 ComfyUI、ComfyUI Desktop bundled runtime，还是只能启动 Desktop 应用；也会显示实际 `main.py` 和 Python 路径，方便确认下载目录和加载目录是不是同一套。
- ComfyUI 安装空间：安装/更新 ComfyUI 和 PyTorch 前会显示安装磁盘剩余空间。
- ffmpeg：闪烁修复需要 `deflicker` 和 `hqdn3d` 滤镜；系统会按 `FFMPEG_PATH`、系统 PATH、`imageio-ffmpeg` 内置二进制的顺序检测，并在环境页显示来源。
- 模型推荐：按文生视频、图生视频、首尾帧、原生音频、固定帧率等能力展示 MiniMax H3、LTX-Video、HunyuanVideo、Wan2.2 与后期模型。
- 缺失项：模型、RIFE 权重、超分权重、自定义节点。
- 磁盘空间：显示目标目录所在磁盘、当前剩余空间、预计还要下载多少，以及建议预留空间。
- 低配 / mac 路线：8GB-12GB CUDA、16GB-24GB、48GB、80GB+、Apple Silicon、CPU 会分别给建议。

如果有缺失项，先在模型控制台选择目标模型，再点击 `一键安装/修复缺失项`。安装范围会自动跟随模型；高级用户仍可手动选择档位：

- `自动推荐`：按当前硬件选择，适合第一次使用。
- `MiniMax H3 原生音视频档`：下载 H3 FL2VA、Qwen3-VL 文本编码器、视频/音频 VAE、可选社区加速和后期工具；原生档仍是默认。
- `LTX 2.5 Distilled INT8`：下载主模型、Gemma 4 12B、DiffVAE/Conv VAE、Audio VAE、潜变量超分器和自动时长头。
- `LTX 2.5 Distilled NVFP4`：面向已确认 NVFP4 兼容的 RTX 50/PRO Blackwell；不确定时选择 INT8。
- `SeedVR2 3B INT8 时序视频超分`：单独下载约 3.69GB 的时序超分模型与 VAE，适合 CUDA 24GB+。
- `LTX-Video 2.3` / `HunyuanVideo 1.5`：下载当前量化档所需模型和后期工具。
- `CUDA 轻量视频包 (Wan2.2 5B)`：Wan 家族的轻量可选包。
- `CUDA Wan2.2 高显存扩展包`：Wan 家族的 A14B/5B 可选包。
- `mac-low` / `mac-balanced`：下载 Mac LTX 路线和后期工具，不下载 A14B。
- `mac-wan5b`：高内存 Apple Silicon 的 Wan2.2 TI2V-5B 实验档。
- `仅后期工具`：只安装 RIFE/超分和示例关键帧，适合先做闪烁修复、插帧、超分。

`自动推荐` 的规则比较保守：NVIDIA 单卡 80GB 以上默认 MiniMax H3，40GB-79GB 默认 LTX-Video 2.5 Distilled INT8，16GB-39GB 默认 HunyuanVideo 1.5 480P FP8，更低显存默认 LTXV 0.9.8。Apple Silicon 按统一内存进入 LTX 或 Wan5B 实验路线；没有可用 GPU/MPS 时进入仅后期路线。你手动选择其他模型后，安装档位和各步骤模型会一起切换。

LTX 2.5 首次安装前必须在 [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) 接受许可，并运行 `hf auth login` 或设置 `HF_TOKEN`。前端会显示这条外部要求；未授权时安装器会停止并保留 `.part`，不会反复重下。LTX 2.5 还要求 ComfyUI `0.32.0+`，旧版本会在环境诊断中被拦截。

填写下载源或代理后，可以直接点击 `测试下载源`。它会先保存当前输入，但不会安装 ComfyUI、不会下载模型，只会按当前 Hugging Face / pip / 代理设置测试 GitHub、Hugging Face 或镜像、PyPI 和 PyTorch 下载源，并把结果刷新到前置条件列表里。

没有 Git 时，前端安装器会自动改用 GitHub ZIP 下载 ComfyUI 和自定义节点源码；ZIP 下载会使用 `.part` 临时文件并自动重试。已有 Git 时会优先 clone/update。ComfyUI、PyTorch 和自定义节点 requirements 的 pip 安装会先确认 pip 可用，必要时自动 `ensurepip`，再执行重试安装。

如果模型或权重已经放在项目上级目录的 ComfyUI `models/` 里，或者你用 `WAN22_LOCAL_ASSET_DIRS` 指向了本地缓存目录，安装器会先复用同名且大小匹配的本地文件。前端缺失项会显示 `本地缓存可用`，这类文件不会再触发 Hugging Face 网络预检。

下载前会做磁盘空间预检。可以在页面选择磁盘或输入绝对路径；容量、缺失项和下载计划会立即按新目录重算，自定义目录会自动注册到 ComfyUI。下载面板显示总进度、当前文件、逐文件百分比、速度与预计剩余时间；暂停、取消都会保留 `.part`，继续时自动断点续传。

ComfyUI / PyTorch 安装也会做空间预检：`auto` 会在 macOS 选择 MPS，检测到 NVIDIA 时选择 CUDA，否则选择 CPU；CUDA 后端建议至少预留 20GB，Mac/CPU 后端建议至少预留 8GB。如果这一步显示空间不足，请把 `COMFY_INSTALL_DIR` 指向空间更大的磁盘，或清理系统盘后再点安装。

安装完成后需要重启 ComfyUI，让模型列表和自定义节点重新加载。

硬件判断规则：

- `96GB 单卡`：默认推荐 MiniMax H3 原生音视频；LTX-Video 2.5 可从 720P、约 5 秒起步，LTX-Video 2.3、HunyuanVideo 1.5 和 Wan2.2 A14B 也可按用途选择。
- `48GB 单卡`：默认推荐 LTX-Video 2.5 Distilled INT8 的 640x352 短镜头，也适合 HunyuanVideo 1.5；Wan TI2V-5B 或 A14B 480P 是可选路线。
- `16GB-24GB 单卡`：默认推荐 HunyuanVideo 1.5 480P FP8 或 LTX 轻量档；Wan2.2 5B 可作为备选。
- `双 48GB`：可以并行跑两个镜头或把部分任务分开跑，但默认不等于一张 96GB 单卡；A14B 720P 仍然主要看单卡显存。
- `macOS Apple Silicon`：会进入独立 Mac 视频路线。8GB/16GB 默认 LTX 低档，24GB-36GB 默认 LTX 均衡/质量档，48GB/64GB 可见 Wan2.2 TI2V-5B 480P 实验档，96GB/128GB+ 可见 Wan2.2 TI2V-5B 720P 高内存实验档。A14B 不作为 Mac 默认推荐。
- `Intel Mac / MPS 不可用`：只推荐关键帧管理、ffmpeg 后期、剪辑和低风险流程。

## macOS Apple Silicon 视频生成模式

Mac 不再只是打开前端和做后期。环境侦测会检查：

- macOS / Apple Silicon / Intel Mac。
- 芯片型号：M1/M2/M3/M4、Pro、Max、Ultra。
- 统一内存大小。
- 前端 Python 里的 `torch.backends.mps` 状态。
- ComfyUI venv 里的 `torch.backends.mps` 状态。
- ComfyUI `/object_info` 里的 LTX / Wan 节点和模型下拉列表。

Mac 推荐顺序：

1. `Mac LTX I2V`：默认路线，优先用关键帧控制画面。
2. `Mac LTX T2V`：没有关键帧时使用，随机性更大。
3. `Mac Wan2.2 TI2V-5B 480P/720P`：只给高内存 Apple Silicon，标为实验档。
4. `Wan2.2 A14B`：Mac 上不默认开放，因为 fp8/MPS dtype 兼容风险高；前端预检会拦截。

Mac 一键安装会按档位下载，不会默认拉完整 A14B；同时会补齐后处理需要的小权重和节点：

- `mac-low` / `mac-balanced`：下载 LTX 2B、T5 XXL、RIFE/超分后期权重、后处理节点和示例关键帧。
- `mac-wan5b`：下载 LTX 2B、T5 XXL、Wan2.2 TI2V-5B、UMT5、Wan2.2 VAE、RIFE/超分后期权重和后处理节点。
- `post-only`：只下载 RIFE/超分后期权重。
- `cuda-full`：Wan2.2 高显存扩展包，下载完整 Wan2.2、后期和超分资产。

如果前端提示“前端 Python 未安装 torch”，不代表 ComfyUI 不能用 MPS。系统会继续检测 ComfyUI venv；如果你用 ComfyUI Desktop，先启动 Desktop 后刷新环境侦测。

Mac 上建议先跑 `Mac LTX I2V 低档` smoke test，再提高分辨率或尝试高内存模型。出现 OOM、dtype、黑屏、节点加载失败时，先退回低分辨率短帧数。

## 模型自选

第一步会自动推荐模型，但后面的步骤都可以在 `当前使用模型` 下拉框里手动切换。切换后会同步：

- 实际提交给后端的 `mode`。
- 步骤右侧显示的模型名。
- 分辨率、帧数、Steps、CFG 等默认参数。
- T2V 模式会隐藏关键帧上传区。
- RIFE 2x / 4x 会同步插帧倍率。
- SeedVR2 2x / RealESRGAN 2x / 4x-UltraSharp 4x 会同步目标分辨率显示和实际超分模型。
- 对应的 ComfyUI API workflow 会自动拉取并预检；预检不过不会提交到 ComfyUI 队列。

每次选择模型或调整关键参数时，`当前使用模型` 下方会显示 workflow 状态：

- `ComfyUI workflow 已创建并预检通过`：可以生成。
- `本步骤使用本地 ffmpeg，不需要 ComfyUI 图`：闪烁修复走本地处理。
- `workflow 预检失败`：通常是 ComfyUI 没启动、节点未加载、模型/自定义节点缺失，先回到「环境与安装」或重启 ComfyUI。

当前前端已经直接接入的生成家族：

- `MiniMax H3 FL2VA`：T2V、首帧/首尾帧 I2V、原生 32kHz 立体声音频，适合 80GB+ CUDA。
- `LTX-Video 2.5`：Distilled INT8/NVFP4、T2V、两阶段 I2V、首尾帧、同步音频、DiffVAE/Conv VAE、自动时长和潜变量超分，适合 48GB+ CUDA。
- `LTX-Video 2.3`：Distilled / Dev FP8 与 NVFP4 档，提供 T2V/I2V 变体。
- `HunyuanVideo 1.5`：480P/720P FP8 档，适合 16GB-48GB CUDA。
- `LTXV 0.9.8`：低显存与 Apple Silicon 的保守短镜头路线。
- `Wan2.2`：TI2V-5B 与 A14B T2V/I2V，作为完整可选家族保留。
- 后期：`ffmpeg deflicker + hqdn3d`、`RIFE 4.9 2x/4x`。
- 超分：默认 `SeedVR2 3B INT8 时序视频超分 2x`；`RealESRGAN x2plus 2x`、`4x-UltraSharp 4x` 为快速兼容档。

下拉框按“模型家族 + 任务能力”分组。上传关键帧后，系统会优先切换到当前家族的 I2V 对应项；移除关键帧后可回到 T2V 对应项。没有对应变体时会明确提示，不会暗中替换成 Wan。

## 参数

- 分辨率：每个模型显示自己的推荐尺寸；草稿优先低分辨率，正式片段再提高。
- 帧率：默认 `24`，模型若固定帧率会锁定并显示原因。
- Steps / CFG：跟随模型预设；切换模型后帮助文字和默认值同步更新。
- `Seed -1` 表示每次随机；填固定数字可以复现同一镜头。
- 闪烁修复默认：`deflicker=s=8:m=pm` 加轻微 `hqdn3d` 时间降噪。
- 清晰度增强默认：`seedvr2_3b_int8_convrot.safetensors`，输出 2x 分辨率视频并联合修复连续帧。没有 CUDA 或显存不足时会自动保留 RealESRGAN 快速兼容选择。
- 清晰度增强界面会显示输入视频分辨率和 2x 输出目标分辨率，例如 `1280 x 704 -> 2560 x 1408`。

MiniMax H3 选择后会显示专用中文参数：

- `H3 运行档位`：默认原生保真；Spectrum 适合批量试镜，LightX v1.1 适合 768P 左右的 4 步快速预览。加速档会自动锁定经过验证的采样器、步数和偏移组合。
- `LightX Turbo 强度`：只对 LightX 4 步档生效，先用 `1.0`，需要时按 `0.05` 小步比较。
- `H3 提示词结构`：可在本地把普通描述整理为官方三段式，不调用云端模型；已经是 H3 结构化提示词时不会重复包装。
- `H3 视频流偏移`：默认 `12.0`，影响大结构和运动收敛；人物不稳时可小幅降到 10-11。
- `H3 音频流偏移`：默认 `3.0`，影响对白、音效和画面的联合去噪，通常不要修改。
- `H3 分块 VAE 解码`：48GB 或长片在解码阶段爆显存时开启；96GB 默认关闭。它降低的是解码峰值，不会降低主模型采样显存。
- `输出 H3 原生立体声音频`：开启后把同步生成的 32kHz 音频封装进 MP4；完全后期配音时可关闭。
- H3 固定输出 `24fps`，输入时长会自动对齐到 `17k+5` 帧。建议先用 `864x480 / 73 帧` 试跑，再升到 `1344x768 / 124 帧`。`1080P 实验横屏` 会按 `1920x1088` 生成并自动裁成 `1920x1080`，96GB 显存也建议先用 `22` 帧、低步数测试。
- H3 Base 是 CFG 蒸馏模型，通用 CFG 保持 `1.0` 即可；负面提示词不参与 H3 的 `BasicGuider`。

LTX-Video 2.5 选择后会显示专用中文参数：

- `视频解码器`：正式出片选 DiffVAE；48GB、快速试镜或解码爆显存时选 Conv VAE。
- `启用两段式空间超分`：默认开启，先在一半分辨率生成，再做潜变量 2x 精修；首尾帧模式会自动使用官方单阶段路径。
- `关键帧约束强度`：默认 `0.7`；人物一致性优先可升到 `0.8` 左右，动作空间不足时降到 `0.5-0.65`。
- `输出附带生成的音频` / `音频提示词强度`：默认输出同步音轨，Distilled 模型的音频 CFG 保持 `1.0`。
- `让模型自动决定时长`：开启后由 Duration Head 在设定的秒数区间内选择时长；关闭时仍使用界面帧数。
- LTX 2.5 帧数会自动对齐到 `8n+1`，宽高按 32 像素对齐。建议从 `640x352 / 25-49 帧` smoke test，再升到 `1280x704 / 121 帧`。

前端每个参数下方都有中文解释：

- `画幅和分辨率`：越大越清晰，也越吃显存和时间。
- `镜头时长`：帧数越多越长，但长镜头更容易漂移。
- `FPS`：24fps 是常见输出帧率，RIFE 2x 会变成 48fps；固定帧率模型不可手动修改。
- `Steps`：采样步数越高通常越慢，蒸馏模型可能只需要较少步数；以当前模型说明为准。
- `CFG`：提示词强度，过高会更僵硬或更容易出伪影。
- `Seed`：随机种子，固定后方便复现和微调。
- `闪烁修复`：主要修亮度、曝光和轻微纹理闪烁；人物变形、Logo 乱跳、物体变来变去需要回到生成阶段重跑。生成失败不会解锁下一步，前端会显示 ComfyUI 节点错误。
- `清晰度增强`：会增加分辨率和文件大小，适合最终交付前处理，不建议在草稿阶段反复跑。

## 输出位置

生成结果会显示在前端右侧，也会保存到：

```text
<ComfyUI base>\output\local_ai_workbench
```

上传给前端的图片和视频会保存到：

```text
<ComfyUI base>\input\beginner_frontend
```

`<ComfyUI base>` 是前端环境页显示的 ComfyUI base 目录；如果你没有手动设置 `COMFY_BASE_DIR`，系统会按当前机器自动选择。

## 已修复的错误

之前 ComfyUI 日志里的错误是：

```text
Prompt has no outputs
```

原因是原工作流顶层缺少 `SaveVideo` 输出节点。现在平台生成器和这些手工模板都已补上输出节点：

- `workflows\ready\01_Wan22_I2V_A14B_720p_4step_READY.json`
- `workflows\ready\02_Wan22_T2V_A14B_720p_4step_READY.json`
- `workflows\ready\05_MiniMax_H3_FL2VA_T2V_INT8_READY.json`
- `workflows\ready\06_MiniMax_H3_FL2VA_I2V_INT8_READY.json`
- `<ComfyUI base>\user\default\workflows\01_Wan22_I2V_A14B_720p_4step_READY.json`
- `<ComfyUI base>\user\default\workflows\02_Wan22_T2V_A14B_720p_4step_READY.json`

前端里的“检查配置”按钮也会验证模型文件、节点和 API 工作流图。

如果需要把问题发给别人排查，右侧结果区有 `复制诊断信息` 按钮。它会汇总前端版本、ComfyUI 连接状态、硬件判断、安装档位、缺失项、磁盘预检和加载诊断，不会读取环境变量或账号密钥。
