# 本地 AI 创作工作台

## 2026-10-23 操作流程更新

- **环境与模型**：首次安装、选模型、检查节点。下载完成不代表可生成，提交前仍会检查对应工作流。
- **图片创作**：文字生图或编辑图片，结果可直接送入视频生成。
- **视频生成**：打开即是生成表单，没有关键帧中转页。先选模型，再填提示词和模型需要的素材，默认只展开常用参数；H3 加速等设置在「全部参数」中。
- **视频处理**：直接处理现有视频。修复与调色、流畅度增强、清晰度增强、拼接均可单独使用，不再是必须依次完成的关卡。
- **作品**：查看当前输出目录中的文件；视频可直接「用于视频后期」，会读取实际宽高和帧率。图片完整历史仍在图片生成页。
- **任务**：跨图片、视频查看任务；按状态和项目筛选全部记录，查看失败原因或取消任务。
- **下载中心**：只管理下载、断点与模型资源，不再混入作品管理。

结果区的「继续处理」把该结果直接交给所选工具，支持超分后再调色等非固定顺序。处理页顶部显示实际输入素材；从作品挑选后会返回原来选中的工具。「移除素材」「新镜头」只清除关联，不删除文件、不取消已有任务。浏览器保存当前入口、处理工具、视频提示词、项目、各操作参数；文件上传刷新后须重新选择。

自动后期只在「视频生成」中勾选，按提交时的步骤及参数执行，不受后续修改影响；失败即停。手动处理一个结果不会偷偷沿用生成页的自动后期勾选。批量生成建议关闭自动后期，逐条在作品中选择处理。自动衔接仍要求页面保持打开。

后端源码版本 `2026.10.23.2`，前端任务式入口更新 `20261023-journey-1`。静态入口兼容旧服务，本次界面更新刷新网页即可生效。新版后端增加静态文件内容指纹，需下次正常重启前端服务后启用，无需重启 ComfyUI。本轮验证为 CPU 测试和模拟接口的浏览器回归，未启动 ComfyUI、未进行 GPU 生成，不能据此宣称所有模型已通过实机推理。

维护者回归：`python -B scripts/self_check.py --json`；安装 Playwright 的开发环境还可运行 `node scripts/test_workbench_frontend.cjs`。后者拦截全部接口，不连接真实后端，覆盖桌面/手机、重复提交、自动后期、参数隔离和刷新恢复。可用 `WORKBENCH_QA_DIR` 指定截图目录。

一个面向小白的本地 AI 视频工作流前端（旧名 Wan22 工作流）。模型目录、安装档位、环境诊断和生成推荐共用同一份清单，可在前端完成 ComfyUI 安装/启动、节点安装、模型下载、视频生成和本地成片拼接。

第一次使用请先看 [START_HERE.md](START_HERE.md)，那里只保留启动、安装、排错和发布前检查的最短路径。

Qwen-Image-2.1 的 Turbo 8 步、新版社区 Q8/INT8、独立 LoRA 与编辑档位见 [QWEN21_UPDATE_GUIDE.md](QWEN21_UPDATE_GUIDE.md)。原有 Qwen、H3、LTX 工作流继续保留。

## 运行模式

打开前端后先到「环境与安装」标签页，顶部有 `运行入口模式`：

- `本机一体`：前端和后端都在这台机器，适合首次开箱。
- `服务端`：这台机器负责安装 ComfyUI、下载模型和生成视频；保存后重启 `start.bat` / `START_WORKFLOW.bat`，局域网电脑可以访问页面显示的 LAN 地址。
- `客户端`：这台机器只作为控制面板；填写服务端地址和访问令牌后，安装、启动、自检、生成、诊断和媒体预览都会调用服务端 API。

服务端/本机一体会自动生成访问令牌。局域网客户端进行写操作时需要令牌；本机浏览器操作不需要额外配置。这个仓库不包含模型文件，也不会把本地 `.local_ai_workbench_config.json` 上传到 GitHub。旧版 `.wan22_workflow_config.json` 会自动兼容读取，后续保存会迁移到新配置文件。

未配置环境时先到「环境与模型」完成准备。日常使用直接进入「图片创作」「视频生成」或「视频处理」。关键帧是图生视频的输入，不再单列为第一步；去闪、RIFE 插帧、SeedVR2 / RealESRGAN 超分、ffmpeg 拼接都是可选处理工具。

前端先按用途展示 `文生视频`、`图生视频`、`首尾帧`、`原生音频` 等能力，再结合 CUDA 显存或 Apple Silicon 统一内存推荐具体模型。模型家族包括 MiniMax H3、LTX-Video、HunyuanVideo 和 Wan2.2；用户可以随时自选。每次切换模型都会同步安装档位、输入控件、参数约束、配套 T2V/I2V 变体和 ComfyUI API workflow，预检失败时不会提交到队列。

视频生成页还可以直接选择 TikTok、Instagram Reels、YouTube Shorts、抖音、小红书、Instagram 4:5、YouTube 横屏或 Bilibili 横屏。`快速试镜 / 均衡成片 / 正式高质量` 会按当前模型分别套用原生 32 像素画布和帧数规则；页面同时显示模型生成尺寸、最终交付尺寸与保守安全区，不会把 `1080 x 1920` 交付尺寸误写成每个模型都应直接生成的尺寸。

## 模型控制台

「环境与安装」页顶部的模型控制台是全局入口。切换目标模型后，安装计划、缺失项、环境卡片、诊断结果以及视频生成步骤的推荐会一起刷新，不再各自维护一套选择。

当前清单包括 MiniMax H3 FL2VA、LTXV 0.9.8、LTX-Video 2.5/2.3、HunyuanVideo 1.5、Wan2.2 5B/A14B，以及 FramePack、SkyReels 等手动接入参考项。每个条目会明确显示三种接入状态：

- `平台原生生成`：平台可以直接构建并提交 API workflow，例如 MiniMax H3、LTX-Video 2.5/2.3、HunyuanVideo 1.5、Wan2.2 与 LTXV 0.9.8。
- `官方 ComfyUI 工作流`：平台可准备所需资产，并指向官方或节点作者提供的工作流。
- `手动接入`：只提供版本、量化、显存建议和来源，不会伪装成可一键安装或原生生成。

### H3 Max by fal（后训练版）

截至 2026-08-27，fal 发布的 H3 Max 是托管的后训练版本，官方入口是 Playground 与 API，尚未提供可下载的 H3 Max checkpoint。因此项目不会用同名第三方权重冒充官方更新，也不会覆盖现有 MiniMax H3 Base 模型。前端已把它作为独立的 `fal 云端 API` 路线接入：支持 T2V、首帧 I2V、首尾帧、5-15 秒、480P/768P 和原生同步音频；提交、排队与结果下载均绕过 ComfyUI，不加载本机视频模型。

Windows 配置 API Key（输入不会回显，密钥不会写入项目）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure_fal_h3_max.ps1
```

配置后只需重启本项目前端，不需要启动或重启 ComfyUI。在模型控制台选择 `H3 Max（fal 后训练版）`，进入视频工作流即可选择 480P 试镜或 768P 正式档。生成结果会自动下载到现有本地输出目录，之后仍可接入去闪、插帧、超分和拼接。`FAL_KEY` 只由后端读取，不会下发给浏览器或写进任务记录。

检查 fal 是否后来发布了官方本地权重：

```bash
python scripts/check_fal_h3_max_release.py
```

检测器只接受 Hugging Face 上所有者精确为 `fal` 且名称匹配 H3 Max 的仓库。只有明确执行下面命令时，才会在发现官方完整权重后下载；默认检测不会下载文件、不会启动 ComfyUI，也不会调用 GPU：

```bash
python scripts/check_fal_h3_max_release.py --download-if-available
```

LTX-Video 2.3 的 FP8 与 NVFP4 使用官方文件名和大小。Gemma 3 12B 文本编码器属于 gated 仓库，需要先在 Hugging Face 接受许可；前端会把这个外部要求显示在模型同步与安装计划中。

### LTX-Video 2.5

LTX-Video 2.5 已作为平台原生 CUDA 路线接入，最低要求 ComfyUI `0.32.0`。前端提供 `Distilled INT8 ConvRot` 和 `Distilled NVFP4` 两档，并直接创建以下 API workflow：

- T2V：文字生成视频和同步音轨。
- I2V：首帧约束，两阶段低分辨率采样 + 潜变量 2x 超分精修。
- 首尾帧：上传尾帧后自动改用官方单阶段 `LTXVAddGuide` 配方。
- 可选 DiffVAE（质量优先）或 Conv VAE（速度/显存优先）。
- 可选 Duration Head 自动决定镜头时长。
- `快速试镜`：单阶段 + Conv VAE；`均衡成片`：两阶段 + Conv VAE；`正式高质量`：两阶段 + DiffVAE。
- `自定义`：只有选择此档时，下面的两阶段超分和解码器开关才直接决定链路，避免一键档静默覆盖手动值。

完整 INT8 安装档约需下载 `38.34 GiB`，包括主模型、Gemma 4 12B INT8 文本编码器、DiffVAE、Conv VAE、Audio VAE、空间潜变量超分器、Duration Head 和后期资产。模型仓库受限：先打开 [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) 点击 `Agree and Access`，再执行 `hf auth login` 或设置 `HF_TOKEN`。安装器会读取登录凭据，401/403 时给出明确指引，并保留 `.part` 断点文件；令牌不会写入项目或诊断包。

48GB CUDA 默认推荐 INT8 的 `640x352` 短镜头，正式片段再提高到 `1280x704`。96GB 可从 720P、约 5 秒开始。`1920x1088` 是 1080P 对齐画布实验档，耗时和显存明显增加；长视频仍建议按 3 到 6 秒分镜生成。NVFP4 优先给已确认兼容的 RTX 50/RTX PRO Blackwell，兼容性不确定时使用 INT8。

MiniMax H3 已拆成两条互不覆盖的本地路线。`FL2VA INT8` 使用指定的 Pruned INT8 ConvRot 扩散模型、Qwen3-VL-32B INT8 文本编码器、视频 VAE 和音频 VAE，可做纯文字、首帧或首尾帧生成，并把 32kHz 立体声音频一起封装进视频。`Ref2VA INT8` 使用独立扩散权重，可组合最多 9 张参考图、3 段参考视频和 3 条参考音频；前端会按类型显示 `<Picture 1>`、`<Video 1>`、`<Audio 1>` 标签供 Prompt 引用。Ref2VA 不会误套 FL2VA LoRA。96GB 显存推荐，40GB-79GB 只作为模型卸载实验档，Mac/MPS 暂不开放。

H3 FL2VA 高级参数提供 `原生保真`、`Spectrum 均衡`、`LightX 8 步 v1.0（768P）`、`LightX 4 步 v1.2（768P）`，并保留 `4 步 v1.1（768P）` 和旧版 `8 步 v1.0（544P）`。新版是独立下载项，不覆盖旧文件，不会自动切换当前模型。Ref2VA 只显示自己的 `Reference 4 步 v0.1（544P）` 实验档。

根据 [LightX 官方参数表](https://github.com/ModelTC/Minimax-H3-Turbo#1-model-specs)，旧版 544P 8 步与 Reference 4 步使用视频/音频 shift `12/3`，768P 档使用 `6/3`。这些是训练档位，不会自动改变画布分辨率。LightX 档锁定配套步数、Euler/simple、shift 和完整去噪；切回原生可恢复手动设置。请勿互换 544P/768P 或 FL2VA/Ref2VA LoRA。

使用入口：选择 H3 模型 → 高级参数 → 社区优化档位。环境检查后会显示“缺少 LoRA”或“LoRA 已齐·待预检”；缺少时进入下载中心，搜索对应完整版本并安装，然后刷新环境、重新预检。文件存在或预检通过不等于已经实测画质。新增 768P LoRA 使用固定仓库版本及 SHA256 校验；本次仅完成 CPU 配置回归，未运行 GPU 生成验证。社区优化输出可能与原生同种子结果不同。平台未启用 ComfyUI 全局 SageAttention，也不做全局注意力替换。

### H3 创作扩展与 INT8 Attention

入口：视频生成步骤选择本地 H3，页面会出现 **H3 创作**。新功能默认关闭，旧工作流不变。

| 任务 | 输入与操作 | 输出与限制 |
| --- | --- | --- |
| 原生质量对照 | 点「恢复原生质量基准」 | 20 步、res_multistep、视频/音频 shift 12/3、PyTorch Attention；关闭加速与创作扩展。FL2VA 用 simple，Ref2VA 用 beta |
| INT8 Attention | 高级参数 → 社区优化档位 → H3 Attention 后端 | 使用原生 ModelAttentionBackend，只作用于当前 H3 模型；Comfy Kitchen 内核未注册时阻止提交，不静默降级 |
| 动作 / 结构控制 | 选择任务，上传控制视频，填写希望生成的场景 | 默认自动提取 Canny；姿态、深度、HED、MLSD 需要自行提供预处理好的控制图视频，不是自动动作捕捉 |
| 局部重绘 | 上传原视频和同宽高的黑白遮罩，提示词描述替换内容 | 白色重绘，黑色保留并与原片合成；静态遮罩用图片，移动遮罩用视频；默认保留原音轨，无声原片保持无声 |
| H3 潜空间超分 | 普通生成任务，勾选潜空间超分，选择最终尺寸 | 低分辨率生成 → 3D 潜空间放大 → 高分辨率精修；显示每段尺寸和最终裁切尺寸，保留第一段音轨 |

例如目标 `1920x1080`，内部画布为 `1920x1088`，1.5 倍路线从 `1280x736` 起步，再精修并裁成标准 1080P。默认精修 8 步、强度 0.35；2 倍更依赖精修，有人物漂移风险。此功能不是导入现成 MP4 后处理，现有成片请使用「清晰度增强」步骤的 SeedVR2。

控制 / 重绘只开放 FL2VA，不能套用 Ref2VA。控制、重绘、潜空间超分仅支持原生采样档，暂不叠加 LightX / Spectrum；控制 / 重绘也不与潜空间超分同时运行。H3 会把时长对齐到 `17n+5` 帧，例如 124 帧约 5.17 秒，控制素材和遮罩视频需覆盖实际对齐后的时长，过短会拦截而不是复制尾帧。

点击「安装创作扩展」只下载独立权重和节点，不下载或替换现有 H3 主模型，不自动启动 ComfyUI。扩展包含约 2.14 GiB 的 Fun ControlNet INT8 权重与约 0.64 GiB 的潜空间超分权重。命令行等价入口：

```bash
python scripts/install_workflow_assets.py --base-dir /path/to/comfy-base --profile minimax-h3-creative-addon --no-node-requirements
```

权重分别存放于 `models/model_patches` 和 `models/latent_upscale_models`，采用固定上游版本和 SHA256 元数据；超分节点固定为 `40316cf008b2fd8663263270669eb4da23f89d2c`。节点安装后需在 GPU 空闲、没有生成任务时手动重启 ComfyUI，刷新环境并检查配置。更新前端需退出旧前端进程后重新启动 `START_WORKFLOW.bat`；本次更新不会代为重启任何服务。

验证边界：已做 CPU 图结构、接口拦截、桌面 / 手机前端回归，不等于实际 GPU 出片通过。上游 [H3 潜空间超分节点](https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler) 属于社区方案，高分辨率精修仍可能占用大量显存，不承诺更清晰或更省显存。控制权重来自 [Comfy-Org H3 模型库](https://huggingface.co/Comfy-Org/MiniMax-H3)。建议首次实测使用 864x480、22 帧，记录原生与扩展的同种子对照。

离线回归：`python -B scripts/test_h3_creative.py`；已有 H3 / LTX 测试：`python -B scripts/test_platform_upgrade.py`。前端回归：在安装 Playwright 的 Node 环境运行 `node scripts/test_h3_frontend.cjs`，所有 API 都是本地模拟，不连接 GPU 服务。

### 社交平台发布优化

1. 先选平台和版式，再选模型。开启 `自动套用模型稳妥参数` 后，切换 H3/LTX/其他模型会重新计算生成画布和帧数。
2. H3 自动对齐 `17n+5`、固定 24 FPS；LTX 2.5 自动对齐 `8n+1`、24 FPS。
3. 页面显示的是两组尺寸：`模型生成` 用于 ComfyUI，`最终交付` 用于超分/拼接后的发布文件。
4. 安全区是保守预览，抖音和小红书仍应以发布客户端的实时裁切为准。
5. `正式高质量` 会建议接去闪和清晰度增强；短视频字幕、封面、响度和平台编码仍建议最后在剪映、达芬奇或 PR 检查。

### 成人内容本地路线与边界

Wan A14B 保留已验证的 High/Low 成对社区 LoRA；H3 和 LTX 2.5 当前采用本地 Prompt 原文路线，不会把 Wan LoRA 错套到其他架构。更稳的跨模型办法是先在独立文生图工作台生成已授权的成年人物关键帧，再用 H3/LTX I2V 动画化。平台不会自动下载来源不明的敏感模型包。

开启成人内容模式必须确认：画面中的人物均为明确的 21 岁以上成年人并自愿参与，操作者拥有素材和肖像授权；不得用于未成年人、年龄含糊者、非自愿内容或未经授权的真人私密仿冒。本地 H3/LTX/Wan 请求留在本机 ComfyUI；选择 fal 等云端模型时，素材和 Prompt 会发送到相应服务商。

H3 的分辨率预设额外提供 `1080P 实验横屏`。模型内部按 32 像素对齐的 `1920x1088` 生成，输出前会居中裁成标准 `1920x1080`。这一档即使在 96GB 显存上也可能触发模型卸载或 OOM，建议先用 `22` 帧和低步数试跑；更稳的成片路线仍是先生成 `1344x768`，再用 SeedVR2 做 2 倍时序超分。

清晰度增强采用 ComfyUI 原生 `SeedVR2` 时序修复。48GB+ CUDA 会优先显示 `7B NVFP4`，其中经过本机验证的 1.5 倍档可把 1280x720 直接输出为 1920x1080；`7B Sharp NVFP4` 作为更锐利的实验档，屏幕、文字和产品边缘可能被过度强化。`3B INT8` 继续作为较低显存的 2 倍档，`RealESRGAN x2plus` 与 `4x-UltraSharp` 保留为更快的逐帧兼容档。

## 独立文生图工作台

顶部 `图片创作` 是独立模块。当前原生接入 Qwen-Image-2.1、Qwen-Image-2512、HiDream-O1-Image Dev-2604 / Full、FLUX.2-dev FP8 / NVFP4、Z-Image Base / Turbo 和 ERNIE-Image 标准 / Turbo 等档位。每个档位会显示发布时间、量化、显存建议、能力、官方工作流、所需文件与当前兼容状态；结果可以直接送入视频生成作为关键帧。

中文提示词默认保留原文，不强制翻译。`结构化增强` 会在保留主体、数量、方位和专有名词的基础上整理镜头、光线与构图；需要在图片中出现准确中文时，可在 `画面文字` 单独填写，系统会附加逐字呈现约束。Qwen、Z-Image 可选社区成人内容 LoRA，必须单独安装后开关才可用；HiDream、ERNIE 和 Bernini-R 暂无已验证专用方案，FLUX.2 因许可要求不集成绕过方案，前端会明确标注而不是显示一个无效开关。

模型下载路径可以在文生图页或环境页选择。容量检查、缺失项、下载任务和 ComfyUI `extra_model_paths.yaml` 使用同一个目录；自定义目录安装完成后重启 ComfyUI 8188 生效。下载面板显示总进度、当前文件、逐文件进度、速度和预计剩余时间，并可暂停、继续或取消；`.part` 文件会保留用于断点续传。

### Qwen-Image-2.1 工作流

图片模块新增 `Qwen-Image-2.1 INT8 ConvRot`，使用 Comfy-Org 的三文件布局，总下载量约 `16.10 GiB`：

- `models/diffusion_models/qwen_image_2.1_int8_convrot.safetensors`
- `models/text_encoders/qwen3vl_8b_int8_convrot.safetensors`
- `models/vae/qwen_image_2.1_vae_bf16.safetensors`

使用步骤：

1. 在「环境与安装」的模型控制台选择 Qwen-Image-2.1，点击一键安装；已有旧版 ComfyUI 时先更新 core，并确认节点列表包含 `TextEncodeQwenImage21` 和 `QwenImage21Cache`。
2. 文生图时选择 `Qwen 2.1 原生 2K` 档。25 步适合预览，官方质量路线可提高到 40 步；CFG 固定为 1。
3. 图片编辑时先导入目标图，它固定对应 `<image1>`；再选择最多 9 张附加参考图，它们依次对应 `<image2>` 到 `<image10>`。
4. 编辑提示词要写清引用关系，例如：`保持 <image1> 的人物和构图，采用 <image2> 的服装材质与 <image3> 的灯光。`
5. 需要透明素材时在提示词中明确要求透明背景或 RGBA，并保留 PNG 输出。编辑输出长宽比跟随第一张目标图，像素量按当前画布上限适配并按 32 像素对齐。

前端提供官方 1:1、4:3、3:4、3:2、2:3、16:9 和 9:16 原生 2K 预设。Qwen-Image-2.1 使用 `Qwen Research License`，发布或商用前请自行核对授权条件。仓库与 GitHub 发布目录只包含工作流和下载清单，不包含上述模型文件。

#### 可选 Q8_0 GGUF 扩展

文生图页选择 `Qwen-Image-2.1 INT8 ConvRot` 后，可单独准备 [abenzerps 的 Q8_0 GGUF](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF) 扩展。先安装上面的官方基础包，再安装 `qwen-image-2.1-uncensored-q8-addon`；基础包不会自动下载此可选权重。

- 新增 `models/diffusion_models/Qwen-Image-2.1-Uncensored/qwen-image-2.1-Q8_0.gguf`，约 7.59 GB（7.07 GiB），下载清单固定版本并带 SHA-256 校验。
- 使用 [leejet/ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF) 的 `UnetLoaderGGUF`，复用已有 Qwen3-VL 8B 编码器和 Qwen 2.1 VAE，不覆盖官方 INT8 文件。
- 首次安装节点后，等当前任务结束再重启 ComfyUI；更新前端代码后也需重新启动前端并刷新页面。文件已下载但节点尚未加载时，开关保持禁用并显示原因。
- 「无限制模式」（NSFW）开关开启时切到社区 Q8_0，关闭时恢复官方 INT8。当前模型名称会同步显示，文生图和多参考编辑使用同一个开关；其他模型工作流不变。

`Uncensored` 是仓库作者的命名，不代表本项目验证过对应效果。该接入只完成下载、文件校验和 CPU 工作流检查，未做 GPU 出图验证。许可仍需按源模型的 `Qwen Research License` 核对；人物授权和内容使用限制不变。

若采样时报 `weight of shape [136] and normalized_shape = [128]`，这是 Q8 小型归一化权重与 ComfyUI 融合 RMS/RoPE 路径的兼容问题，不是分辨率或显存不足。安装器会在固定版本的 GGUF 加载器中加入仅针对 Qwen 2.1 的修复：CPU 解码 65 个一维向量（约 48 KiB），主体矩阵仍保持 Q8；原 `loader.py` 会备份为 `loader.py.before-qwen21-norm-fix`。安装器遇到不匹配的加载器代码会停止，不强行覆盖。修复后需要重启 ComfyUI 清除旧加载器和已缓存模型，刷新网页不能替代重启。

可用 ComfyUI 自己的 Python 进行真实模型小权重检查，不会启动 ComfyUI、初始化 CUDA 或执行完整出图：

```powershell
& "<COMFY_PYTHON>" -B scripts/test_qwen_image_21_gguf_cpu.py --gguf-dir "<COMFY_BASE_DIR>/custom_nodes/ComfyUI-GGUF" --model "<COMFY_BASE_DIR>/models/diffusion_models/Qwen-Image-2.1-Uncensored/qwen-image-2.1-Q8_0.gguf"
```

```powershell
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile qwen-image-2.1-uncensored-q8-addon --dry-run
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile qwen-image-2.1-uncensored-q8-addon --comfy-python "<COMFY_PYTHON>"
```

视频模型目录新增 ByteDance Bernini-R FP8。平台可一键准备双专家权重、UMT5、VAE、LightX2V LoRA 与后期资产，并明确使用 ComfyUI 0.28 官方 Bernini 模板；48GB 显卡只标为短片实验路线，官方推荐的 H100/H800/H200 仍会保留在兼容提示中。

「环境与安装」页会优先读取正在运行的 ComfyUI `--base-directory`、`--input-directory`、`--output-directory`；如果默认启动没有 `--base-directory`，也会尝试从 `/system_stats` 的 `main.py` 路径推断 ComfyUI base。这对 ComfyUI Desktop 和 macOS 很重要：如果 ComfyUI 实际扫描的目录和前端默认目录不同，前端会自动把模型、节点、上传文件和输出预览切到 ComfyUI 正在使用的目录。

## 不包含什么

本仓库不包含模型文件、输出视频、ComfyUI 本地用户数据或虚拟环境。

`.gitignore` 已排除：

- `models/`
- `input/`
- `output/`
- `temp/`
- `custom_nodes/`
- `*.safetensors`
- `*.pth`
- `*.gguf`
- 视频和图片输出文件

## 推荐目录结构

最省心的方式：把本项目放在 ComfyUI 用户/base 目录的子目录里。

```text
ComfyUI-base/
  models/
  input/
  output/
  custom_nodes/
  <project-folder>/
```

如果你的项目不在这个位置，可以设置 `COMFY_BASE_DIR` 指向 ComfyUI 用户/base 目录。

如果 ComfyUI 已经在运行，前端会优先使用 ComfyUI 自己报告的目录；如果 ComfyUI 未连接，才会回退到 `COMFY_BASE_DIR`。

未设置 `COMFY_BASE_DIR` 时，启动器会先判断项目父目录是否已经像 ComfyUI base（同时存在 `models/`、`input/`、`output/`、`custom_nodes/`）。只有满足这个条件才使用父目录；否则使用项目目录自己，避免在 macOS 桌面等位置误创建一堆模型目录。

也可以设置 `COMFY_INSTALL_DIR` 指向 ComfyUI 源码安装目录；不设置时默认是：

```text
<COMFY_BASE_DIR>/ComfyUI
```

如果这个默认目录已经非空且不像 ComfyUI 源码目录，前端会自动退回项目内 `ComfyUI/`，避免被同级旧目录卡住。显式设置 `COMFY_INSTALL_DIR` 时会尊重你的配置。

## 环境要求

- Python 3.10-3.12。启动器会自动创建项目 `.venv`、修复缺失 pip 并安装前端依赖。
- ComfyUI 可以还没安装；前端第一步可以安装/更新并启动源码版 ComfyUI。
- ComfyUI/PyTorch 的 `auto` 后端会在 macOS 选择 MPS，检测到 NVIDIA 时选择 CUDA，否则选择 CPU，避免无 NVIDIA 的 Windows/Linux 机器误装 CUDA 大包。
- ComfyUI-VideoHelperSuite、ComfyUI-Frame-Interpolation 和后处理权重会随当前档位一键安装。
- Git 不是硬性前置条件；没有 Git 时安装脚本会改用 GitHub ZIP 下载 ComfyUI 和自定义节点源码，ZIP 下载会使用 `.part` 临时文件并自动重试。已有 Git 时会优先 clone/update；如果首次 `git clone` 失败，脚本会清理半截目录并自动退回 ZIP 下载。
- 如果模型或权重已经放在项目上级目录的 ComfyUI `models/` 里，或者你用兼容变量 `WAN22_LOCAL_ASSET_DIRS` 指向了本地缓存目录，安装器会先复用同名且大小匹配的本地文件；找不到本地文件时才会下载。
- ffmpeg 用于闪烁修复和第 8 步成片拼接；系统会按 `FFMPEG_PATH`、系统 PATH、`imageio-ffmpeg` 内置二进制的顺序检测，并在环境页显示来源。
- NVIDIA CUDA 与 Apple Silicon 都会进入多模型策略；平台按可用显存、统一内存、后端和模型能力给出默认项，Wan 不再是固定主线。

硬件粗略建议：

- 8GB-12GB CUDA：LTXV 0.9.8 低分辨率短镜头；其他模型仅按实测手动尝试
- 16GB-24GB CUDA：HunyuanVideo 1.5 480P FP8 或 LTX 轻量档；Wan2.2 5B 可作为备选
- 40GB-79GB CUDA：默认 LTX-Video 2.5 Distilled INT8；HunyuanVideo 1.5 与 Wan A14B 480P 可选
- 80GB-96GB CUDA：默认 MiniMax H3 原生音视频，也可选 LTX-Video 2.5、LTX-Video 2.3 或 Wan2.2 A14B
- 8GB/16GB Apple Silicon：Mac LTX 低档，512x320 或更低，1-2 秒 smoke test
- 24GB-36GB Apple Silicon：Mac LTX 均衡/质量档，Wan5B 低分辨率入口可见但不默认
- 48GB/64GB Apple Silicon：Mac Wan2.2 TI2V-5B 480P 实验档
- 96GB/128GB+ Apple Silicon：Mac Wan2.2 TI2V-5B 720P 高内存实验档
- Intel Mac / MPS 不可用：只推荐后期、剪辑和低风险流程

## macOS 视频生成模式

环境侦测会把 macOS 变成平台策略，而不是只显示“可实验”：

- `mac_chip`：M1/M2/M3/M4、Pro、Max、Ultra。
- `unified_memory_gb`：统一内存大小。
- `front_torch_mps_ready`：前端 Python 是否有可用 MPS。
- `comfy_torch_mps_ready`：ComfyUI venv 是否有可用 MPS。
- `/object_info`：验证 LTX / Wan 节点和模型下拉列表。

Mac 推荐顺序：

1. LTX 2B I2V：默认路线，优先关键帧控制画面。
2. LTX 2B T2V：没有关键帧时使用。
3. Wan2.2 TI2V-5B：48GB+ 作为实验档，96GB/128GB+ 可尝试 720P 短镜头。
4. Wan2.2 A14B：只保留专家风险提示，不作为 Mac 默认推荐，因为 fp8/MPS dtype 兼容风险较高。

如果前端 Python 没装 torch，不代表 ComfyUI 不能用 MPS。前端会继续检测 ComfyUI 的 Python；使用 ComfyUI Desktop 时，先启动 Desktop 后刷新环境侦测。

## 启动器会自动安装前端依赖

通常不需要手动执行下面命令。第一次运行 `START_WORKFLOW.bat`、`START_WORKFLOW.ps1` 或 `START_WORKFLOW.command` 时，启动器会自动创建 `.venv`，先用 `ensurepip` 修复缺失 pip，再优先执行 `pip install -r requirements.lock`；锁文件缺失时才回退到 `requirements.txt`。pip 下载失败会自动重试。Windows 推荐双击 `.bat`，它会用 `ExecutionPolicy Bypass` 调用 `.ps1`，避免 PowerShell 执行策略拦住第一次启动。如果 Windows 没有 Python 但有 winget，启动器会询问是否安装 Python 3.12，并使用 winget 协议确认参数减少首次安装卡顿；安装后还会额外检查常见 Python 安装目录，减少 PATH 尚未刷新的失败。如果 Windows 没有 winget，会提示官方 Python 下载页。macOS 没有 Python 时，启动器会检查常见 Homebrew/python.org 安装路径；有 Homebrew 时会询问是否安装 `python@3.12`，没有 Homebrew 但有 `curl` 时会询问是否安装 Homebrew，无法自动安装时会提示或打开官方 Python macOS 下载页。启动器安装前端依赖时会复用本地 `.local_ai_workbench_config.json` 里的代理和 pip 镜像设置，并兼容读取旧版配置；如果前端依赖安装多次失败，启动器会直接询问是否填写 pip 镜像或 HTTP 代理，保存后自动重试一次。

如果第一次启动卡在前端依赖安装，网页还没打开，优先按启动窗口提示填写 pip 镜像或代理。命令行备用方式如下：

```bash
python START_WORKFLOW.py --set-pip-index https://pypi.org/simple
python START_WORKFLOW.py --set-proxy http://127.0.0.1:7890
python START_WORKFLOW.py --show-download-settings
```

Windows 可把 `python START_WORKFLOW.py` 换成 `START_WORKFLOW.bat`；macOS 可换成 `./START_WORKFLOW.command`。需要清空这些本地设置时运行 `python START_WORKFLOW.py --clear-download-settings`。

如果你想手动准备前端 Python 环境，可以在项目目录运行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python -m ensurepip --upgrade
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

macOS / Linux：

```bash
./.venv/bin/python -m ensurepip --upgrade
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -r requirements.txt
```

如果你已经有 ComfyUI 自带的 Python 环境，也可以直接用那个 Python 启动前端。

启动器会优先使用项目目录里的 `.venv`。平台端口固定为 `7860`；若被旧版或其他进程占用，启动器会明确提示先停止占用进程，不会悄悄换端口。

如果没有手动设置 `COMFY_URL`，启动器和前端默认使用 ComfyUI 端口 `8188`，并自动探测 `8000`/`8188`。这能兼容本前端启动的 ComfyUI，也能兼容 ComfyUI Desktop 或默认端口服务。手动设置 `COMFY_URL` 时，系统会严格使用你的配置。

## 先启动前端

你只需要先让这个前端跑起来。即使 ComfyUI 没安装或没启动，前端也能打开，并在「环境与安装」页提示怎么装。

Windows 最简单方式：双击 `START_WORKFLOW.bat`。

Windows PowerShell：

```powershell
.\START_WORKFLOW.ps1
```

macOS / Linux：

```bash
chmod +x ./START_WORKFLOW.command
./START_WORKFLOW.command
```

如果压缩包或网页上传让 `.command` 丢失执行权限，重新运行上面的 `chmod +x` 即可；也可以直接执行 `bash ./START_WORKFLOW.command`。

或直接：

```bash
python START_WORKFLOW.py
```

## 一键自检

不下载模型、不启动真实生成，只检查代码、manifest、workflow 图、无 Git 兜底和发布包卫生：

```bash
python scripts/self_check.py
```

通过时会显示 `SELF CHECK OK`。这个命令可在 GitHub 下载后的项目目录直接运行。

本轮 H3/LTX/社交平台升级的纯 CPU 回归检查：

```bash
python -m scripts.test_platform_upgrade
```

它只解析源码与工作流图，不连接 ComfyUI、不导入 Torch、也不提交 GPU 推理。

更接近新机器的发布烟测：

```bash
python scripts/release_smoke.py --json
```

它会把发布包复制到临时目录，检查启动器、安装器 dry-run、前端关键 API 和诊断接口；不会下载模型，也不会写入真实 ComfyUI 目录。

如果要进一步验证“本地完全没有项目 `.venv`”时也能首启，可以运行：

```powershell
python scripts/clean_bootstrap_smoke.py --json
```

它会复制发布包到临时目录，真实创建一个全新的 `.venv`、安装前端依赖并启动前端；需要 PyPI 或 pip 镜像可达，但不会下载视频模型。

启动后打开：

```text
http://127.0.0.1:7860
```

如果 ComfyUI 计划使用非默认地址：

```bash
COMFY_URL=http://127.0.0.1:8188 python START_WORKFLOW.py
```

Windows PowerShell：

```powershell
$env:COMFY_URL="http://127.0.0.1:8188"
.\START_WORKFLOW.ps1
```

## 在前端里安装/启动 ComfyUI

进入前端后，「环境与安装」标签页会显示 `ComfyUI 安装与启动`。

你可以直接在页面里操作：

1. 先在模型控制台选择目标模型，再看同步生成的硬件策略和安装档位。
2. 小白推荐直接点击 `一键准备环境`：如果 ComfyUI 未连接，它会负责 ComfyUI，再按当前模型档位安装节点、模型、RIFE 和超分权重并尝试启动；如果已有 ComfyUI 正在运行，它会跳过源码安装，把资产装到 active base。
3. `一键安装/修复缺失项` 不负责安装或重建 ComfyUI，只补当前档位的模型、节点和后期权重。手动接入模型会同时禁用这两个按钮，并显示模型来源与外部要求。
4. 安装节点或模型后，如果页面提示需要重启 ComfyUI，就重启 ComfyUI，再回到「环境与安装」点击 `重新侦测`。
5. 点击 `生成链路测试`：前端会选择当前环境可用的最保守视频模型，提交一个短视频任务，验证 ComfyUI 队列、模型名、节点和输出目录真的能跑通。

`自动推荐` 会按当前硬件保守选择：NVIDIA 单卡 80GB 以上默认 MiniMax H3 原生音视频档，40GB-79GB 默认 LTX-Video 2.5 Distilled INT8，16GB-39GB 默认 HunyuanVideo 1.5 480P FP8，更低显存默认 LTXV 0.9.8 轻量档。Apple Silicon 会按统一内存进入 LTX 或高内存 Wan5B 实验路线；只做后期时可选 `仅后期工具`。你在模型控制台手动选择其他模型后，安装档位会立即跟随选择，不再被硬件默认覆盖。

点击 `运行本机自检` 可以在前端里执行无下载检查：前置条件检测和项目自检会在后台运行，结果会写入日志和诊断包，不需要打开命令行。

`运行本机自检` 不下载模型、不提交生成；`一键准备环境` 会真实安装 ComfyUI、节点和模型；`生成链路测试` 会真实提交一个低成本短视频任务。首次装完后建议先跑 `生成链路测试`，通过后再进入关键帧和正式分镜。

真实安装开始前会先通过 HTTPS 检查本次档位实际需要的下载源，可能包括 GitHub、Hugging Face、PyPI 和 PyTorch 下载源。已经就绪的模型/节点不会触发不必要的站点阻断；前端显示 `本地缓存可用` 的模型也不会触发 Hugging Face 阻断，安装时会先复用本地文件。如果本次真正需要的下载源不可达，前端会直接停止任务并给出代理、DNS、防火墙和断点续传建议，不会等到大模型下载中途才报错。ComfyUI、PyTorch 和自定义节点 requirements 的 pip 安装会先确认 pip 可用，必要时自动 `ensurepip`，再执行重试安装。

一键安装前会做磁盘空间预检：完整文件会跳过，`.part` 续传文件只计算剩余体积，并额外预留 10GB 缓冲。空间不足时前端会禁用安装按钮，后端接口也会直接返回失败任务，不会开始下载。可以直接在页面选择其他磁盘或输入绝对路径；自定义路径会写入 ComfyUI `extra_model_paths.yaml`。

模型下载时前端会显示总量、当前文件、逐文件百分比、速度和预计剩余时间。点击暂停或取消不会删除 `.part`；点击继续或重新安装会从剩余字节续传。如果 `.part` 已完整，安装脚本会直接转正，不再重复请求网络。

如果 Hugging Face、PyPI 或 PyTorch 下载源在你的网络里不可达，可以在「环境与安装」页的 `下载源与代理` 里填写 Hugging Face 镜像地址、pip 镜像地址，或本机 HTTP 代理，例如 `http://127.0.0.1:7890`。这些设置会保存到本地 `.local_ai_workbench_config.json`，不会进入 GitHub；一键准备、ComfyUI 安装、PyTorch/pip 依赖安装、安装缺失项、网络预检和诊断都会使用同一套网络设置。诊断信息会脱敏代理里的账号密码。高级用户也可以用环境变量：

填写下载源或代理后，可以直接点击 `测试下载源`。它会先保存当前输入，但不会安装 ComfyUI、不会下载模型，只会按当前设置测试 GitHub、Hugging Face 或镜像、PyPI 和 PyTorch 下载源，并把结果刷新到前置条件列表里。第一次准备环境前建议先点一次，尤其是 Hugging Face 在你当前网络里不稳定时。

```bash
LOCAL_AI_HF_ENDPOINT=https://your-hf-endpoint.example python START_WORKFLOW.py
```

或：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 python START_WORKFLOW.py
```

或：

```bash
PIP_INDEX_URL=https://pypi.org/simple python START_WORKFLOW.py
```

环境变量优先级高于页面保存的设置；页面会显示当前生效来源。

如果安装任务失败，前端日志会自动显示失败类型和下一步建议；点击右侧 `下载诊断包` 或 `复制诊断信息` 时，也会带上最近安装任务的失败分类和日志尾部，方便远程排查。诊断包会脱敏代理地址里的账号密码。

安装/更新 ComfyUI 也会做空间预检。CUDA 后端建议至少预留 20GB，Mac/CPU 后端建议至少预留 8GB；如果空间不足，请清理磁盘，或用 `COMFY_INSTALL_DIR` 指向更大的安装盘。

安装 ComfyUI 使用的脚本是：

```text
scripts/install_comfyui.py
```

默认 PyTorch 后端为 `auto`：

- macOS：选择 MPS/普通 PyTorch。
- Windows/Linux 检测到 NVIDIA：选择 CUDA PyTorch。
- Windows/Linux 未检测到 NVIDIA：选择 CPU PyTorch，避免无显卡机器误装 CUDA 大包。

高级用户也可以手动运行：

```bash
python scripts/install_comfyui.py --base-dir "<COMFY_BASE_DIR>" --install-dir "<COMFY_INSTALL_DIR>" --backend auto
```

只预演安装计划和磁盘空间，不实际安装：

```bash
python scripts/install_comfyui.py --base-dir "<COMFY_BASE_DIR>" --install-dir "<COMFY_INSTALL_DIR>" --backend auto --dry-run
```

`--backend` 可选：`auto`、`cuda`、`cpu`、`mps`、`skip`。`auto` 会在 macOS 选择 MPS，检测到 NVIDIA 时选择 CUDA，否则选择 CPU。

## 发布前验收

上传 GitHub 前建议在发布目录运行：

```bash
python scripts/prerequisite_doctor.py --json
python scripts/self_check.py --json
python scripts/test_platform_upgrade.py
python scripts/release_smoke.py --json
python scripts/clean_bootstrap_smoke.py --json
python START_WORKFLOW.py --check
```

H3 界面回归：开发环境已提供 Playwright 和 Chromium 时，运行 `node scripts/test_h3_frontend.cjs`。可用 `TEST_PYTHON` 指定已安装前端依赖的 Python；默认使用项目 `.venv`。测试拦截全部 API、禁用浏览器 GPU，只验证桌面/手机控件、参数请求和档位切换，不连接真实 ComfyUI；截图输出到 `output/playwright/h3-profiles/`，不要加入发布包。

Windows 还可以额外检查双击入口：

```powershell
.\START_WORKFLOW.bat --check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\START_WORKFLOW.ps1 --check
```

这些检查不会下载模型，也不会启动真实生成。

## 下载模型和工作流资产

先确认 `COMFY_BASE_DIR` 指向 ComfyUI 用户/base 目录。项目放在 base 目录子文件夹时可以不设置。

Windows PowerShell 示例：

```powershell
$env:COMFY_BASE_DIR="D:\ComfyUI"
.\.venv\Scripts\python .\scripts\install_workflow_assets.py --base-dir $env:COMFY_BASE_DIR
```

macOS / Linux 示例：

```bash
export COMFY_BASE_DIR="$HOME/ComfyUI"
./.venv/bin/python ./scripts/install_workflow_assets.py --base-dir "$COMFY_BASE_DIR"
```

如果你知道 ComfyUI 自己的 Python 路径，建议传给安装脚本，这样会自动安装自定义节点 requirements：

```bash
./.venv/bin/python ./scripts/install_workflow_assets.py \
  --base-dir "$COMFY_BASE_DIR" \
  --comfy-python "$COMFY_INSTALL_DIR/.venv/bin/python"
```

这个脚本支持安装档位：

```bash
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile mac-low
```

建议先预演一次，不下载文件，只查看当前档位、预计下载体积和磁盘空间：

```bash
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile mac-low --dry-run
```

可选 `--profile`。通常直接在前端选择模型即可，下面的旧档位和专项档位主要用于命令行兼容：

- `minimax-h3-fl2va-int8`：MiniMax H3 FL2VA 原生音视频模型、LightX 4/8 步 LoRA、三种官方小型效果 Embedding、固定版本 Spectrum 节点与后期资产。
- `minimax-h3-ref2va-int8`：独立 Ref2VA INT8 模型、共享文本/VAE、Reference 4 步 LoRA、效果 Embedding 与后期资产；不会覆盖 FL2VA 文件。
- `ltx25-distilled-int8`：LTX 2.5 Distilled INT8 完整原生音视频档，约 38.34GiB。
- `ltx25-distilled-nvfp4`：LTX 2.5 Distilled NVFP4 档，适合已确认 NVFP4 兼容的 Blackwell CUDA 环境。
- `qwen-image-2.1-int8`：Qwen-Image-2.1 INT8 ConvRot 文生图、多参考编辑与透明图档，约 16.10GiB。
- `qwen-image-2.1-uncensored-q8-addon`：可选 Qwen 2.1 Q8_0 GGUF 扩展，约 7.07GiB，需先安装官方基础包。
- `seedvr2-upscale`：SeedVR2 3B INT8 时序视频超分与 FP16 VAE，约 3.69GB。
- `seedvr2-upscale-7b`：SeedVR2 7B NVFP4 保真时序超分与 FP16 VAE，96GB 推荐档。
- `seedvr2-upscale-7b-sharp`：SeedVR2 7B Sharp NVFP4 实验超分与 FP16 VAE，建议先对单镜头试跑。
- LTX-Video / HunyuanVideo 专项档：由前端模型控制台自动选择对应 profile。
- `cuda-full`：Wan2.2 高显存扩展包，下载 A14B、5B、RIFE 和超分资产。
- `cuda-wan5b`：Wan2.2 轻量扩展包，下载 TI2V-5B、UMT5、Wan VAE 和后期资产。
- `mac-low` / `mac-balanced`：下载 LTX 2B、T5 XXL、RIFE/超分后期权重、后处理节点和示例关键帧。
- `mac-wan5b`：下载 LTX 2B、T5 XXL、Wan2.2 TI2V-5B、UMT5、Wan2.2 VAE、RIFE/超分后期权重和后处理节点。
- `post-only`：只下载 RIFE/RealESRGAN 快速后期权重；SeedVR2 需选择独立档位。

前端的一键安装默认会自动选择档位，也可以在页面里手动切换；手动运行脚本时才需要显式传 `--profile`。

LTX 2.5 命令行安装示例：

```powershell
hf auth login
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile ltx25-distilled-int8 --dry-run
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile ltx25-distilled-int8 --comfy-python "<COMFY_PYTHON>"
```

Qwen-Image-2.1 命令行安装与无下载预检：

```powershell
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile qwen-image-2.1-int8 --dry-run
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile qwen-image-2.1-int8 --comfy-python "<COMFY_PYTHON>"
python scripts/test_qwen_image_21.py
```

MiniMax H3 本地路线命令行示例：

```powershell
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile minimax-h3-fl2va-int8 --dry-run
python scripts/install_workflow_assets.py --base-dir "<COMFY_BASE_DIR>" --profile minimax-h3-ref2va-int8 --dry-run
```

去掉 `--dry-run` 才会真实下载。两个档位共享文本编码器与 VAE，安装器会复用已存在且校验通过的文件。

`cuda-full` 会下载或校验：

- Wan2.2 TI2V-5B
- Wan2.2 I2V-A14B fp8 high/low
- Wan2.2 T2V-A14B fp8 high/low
- UMT5 文本编码器
- Wan VAE
- Lightx2v 4-step LoRA
- RIFE 4.9 权重
- RealESRGAN x2plus
- 4x-UltraSharp
- SeedVR2 3B INT8 与视频 VAE（独立 `seedvr2-upscale` 档）
- 示例关键帧
- 缺失的两个 ComfyUI 自定义节点仓库

下载完成后，重启 ComfyUI，让模型列表和自定义节点重新加载。

## 使用流程

1. 启动前端。
2. 首屏 `环境与模型` 会自动侦测；点击 `一键准备环境`，或者手动安装/启动 ComfyUI 并补齐当前档位资产。
3. 点击 `生成链路测试`，确认已经能真实出一个短视频。
4. 在 `关键帧` 上传或确认首帧图片。
5. 用当前模型的低分辨率或短时长参数生成草稿；有关键帧时，前端会自动切到同家族图生视频变体。
6. 草稿满意后在同一步提高分辨率/时长，或切换高质量档。H3 可在提示词中同时写对白、音效和环境声。
7. 视情况做闪烁修复、RIFE 插帧、清晰度增强，再拼接多个镜头。

输出默认保存到：

```text
<COMFY_BASE_DIR>/output/local_ai_workbench
```

上传的图片和视频默认保存到：

```text
<COMFY_BASE_DIR>/input/beginner_frontend
```

## 工作流文件

`workflows/ready/` 里包含可直接导入 ComfyUI 的参考工作流。

- `05_MiniMax_H3_FL2VA_T2V_INT8_READY.json`：纯文字生成原生音视频。
- `06_MiniMax_H3_FL2VA_I2V_INT8_READY.json`：首帧或首尾帧生成原生音视频。

H3 Ref2VA 因参考图片/视频/音频的数量是动态的，由前端按本次上传素材实时创建 API workflow，不放一个写死引用数量的 `READY.json`。在视频生成页选择 `MiniMax H3 Ref2VA INT8 多参考素材`，上传素材后点 `检查配置`，即可看到节点、模型和引用输入的预检结果。

`workflows/mac/` 里包含 Mac LTX I2V/T2V、LTX smoke test、Mac Wan5B 480P API workflow 样例。

`workflows/comfyui_blueprints/` 里保留官方/蓝图版本，方便对照和二次编辑。

前端实际提交时会动态生成 ComfyUI API workflow，并在提交前做节点、模型名、输出节点和 Mac/MPS 风险预检。

## 常见问题

### 前端提示 ComfyUI 未连接

先回到「环境与安装」标签页。推荐顺序是：点击 `一键准备环境`；如果已经装好但未运行，就点 `启动 ComfyUI` 并等状态变成“已连接”；最后点击 `生成链路测试`。如果你手动改过端口，再确认 `COMFY_URL` 是否指向正在运行的 ComfyUI。

### workflow 预检失败

常见原因：

- ComfyUI 没启动
- 自定义节点没安装或没重启
- 模型文件缺失
- ComfyUI 版本节点名变化

先回到「环境与安装」标签页查看缺失项。

### 需要别人帮忙排查

点击右侧结果区的 `下载诊断包`，把下载的 JSON 文件发给维护者；也可以点 `复制诊断信息` 直接粘贴到聊天窗口。它会汇总前端版本、ComfyUI 连接状态、硬件判断、安装档位、缺失项、磁盘预检、加载诊断和最近任务日志尾部，并会脱敏代理地址里的账号密码。

### macOS / ComfyUI Desktop 下载了但没有加载

先看第一步 `加载诊断`：

- 如果显示目录不一致，说明文件下载到了一个目录，而 ComfyUI 实际扫描另一个目录。重新点击 `一键安装/修复缺失项`，前端会安装到运行中的 ComfyUI active base。
- 如果显示自定义节点目录存在但节点未加载，通常是节点 requirements 没装进 ComfyUI Python 环境，或安装后没有重启 ComfyUI。重新点击 `一键安装/修复缺失项`，完成后重启 ComfyUI。
- 如果模型文件已存在但没有出现在 ComfyUI 模型列表，通常需要重启 ComfyUI；仍不出现时检查 `加载诊断` 里的 active base/input/output 是否就是你当前 ComfyUI 使用的目录。

ComfyUI Desktop 管理 Python 环境的方式可能和源码版不同。如果脚本提示找不到 ComfyUI Python，请在 Desktop 的自定义节点管理器里安装节点依赖，或改用前端的 `安装/更新 ComfyUI` 创建源码版 ComfyUI。

如果 ComfyUI Desktop 已经连接，`一键准备环境` 默认不会再安装另一份源码版 ComfyUI，也不会把本项目源码版 ComfyUI 的 Python 当成 Desktop 的节点环境。模型和节点会放入 Desktop 正在使用的 active base；节点依赖仍可能需要在 Desktop 管理器中安装并重启。

### 48GB 显卡能不能跑 A14B 720P

默认不推荐。48GB 更适合 A14B 480P、短镜头、offload，或者用 TI2V-5B 720P 做主力。

### 双 48GB 是否等于一张 96GB

默认不是。大多数单个 ComfyUI 视频任务主要看单卡显存。双卡更适合并行跑镜头，或做更复杂的多进程配置。

## 贡献者

- [xu0005HE@E.NTU.EDU.SG](mailto:xu0005HE@E.NTU.EDU.SG)
