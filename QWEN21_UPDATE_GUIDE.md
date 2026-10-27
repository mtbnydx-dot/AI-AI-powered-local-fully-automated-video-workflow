# Qwen-Image-2.1 更新与使用说明

核对日期：2026-10-16。平台版本：2026.10.16.1。

## 本轮部署

所有新增文件均使用固定 Hugging Face revision，已逐文件核对 SHA256。新增约 20.92 GiB；编码器和 VAE 复用现有文件，不重复下载。

| 图片模型菜单中的档位 | 用途 | 采样配置 | 状态 |
| --- | --- | --- | --- |
| Qwen 2.1 Turbo 8 步 | 官方快速生成与多参考编辑 | 8 步 / CFG 1 / Euler / Simple | 权重与静态图已核验，未出图实测 |
| 社区 UC Q8 / 2026-10 | 独立新版 Q8 扩展 | 25 步 / CFG 1 | 社区模式开启后使用新版 UC Q8 |
| 社区 UC INT8 / 2026-10 | 使用原生 UNETLoader 的社区扩展 | 25 步 / CFG 1 | 不依赖 GGUF 加载器 |
| 社区 UC LoRA / 实验 | 在官方基础权重上加载轻量社区适配器 | 25 步 / CFG 1；强度默认 0.8 | 作者未提供充分效果验证，保留实验标记 |
| 保构图编辑 25 步 | 降低编辑时的画面偏移 | 25 步；Consistency 强度 1.0 | 仅在“已有图片微调”中开放 |
| 照片修复 25 步 | 模糊、压缩损伤照片的修复试用 | 25 步；Restore 强度 0.5 | 仅图片微调；目标像素限制约 2 MP |

**没有启动或重启 ComfyUI，没有调用 GPU，没有提交生成任务，也没有卸载其他模型。** 当前安装的 ComfyUI 0.36.0 未升级。所有效果、速度和显存占用都需要后续真实生成验收，不把文件完整当成效果已验证。

## 在前端使用

1. 等显卡可用于生图后，再通过原有 `start.bat` / `START_WORKFLOW.bat` 入口启动平台。需要保留显卡给文本模型时，不要启动生成服务。
2. 打开“图片生成”，点击“重新检查”。选择表格中的档位。原来的 Qwen 2.1 原生 2K 档及旧 Q8 开关仍保留。
3. 官方快速生成选择“Qwen 2.1 Turbo 8 步”。步数、CFG、采样器与调度器按档位固定，不会把普通 25 步 Q8 错当作 Turbo 使用。
4. 社区档默认仍为官方标准模式；明确确认主体为成年人、内容合规且参考素材已获授权后，再打开现有社区/成人扩展开关。关闭开关恢复官方基础图，不修改模型文件。
5. UC LoRA 可以通过现有强度滑块调整，0 表示完全旁路。0.5–1.0 是平台试用范围，不是作者经过验证的最佳值。不要在已融合的 UC 模型上再次叠加同一 UC LoRA。
6. 保构图与修复档先切换“已有图片微调”，上传目标图后再选择。当前沿用平台按目标像素预算缩放参考图的原生编辑路径，不承诺原尺寸逐像素对齐；附加参考图顺序仍是 `<image1>` 为目标图，后续为补充参考。
7. 生成完成后，在图片历史查看和导出结果。首次只做一张普通合规测试图，确认实际加载、输出尺寸及错误日志，再扩大批量。

ComfyUI 未连接时，社区开关和生成按钮应保持未就绪。文件存在但节点未注册时，不要反复下载；连接 ComfyUI 并刷新检查。此次部署只写入磁盘，未做运行时节点注册验收。

## 新文件位置

以下路径相对于 ComfyUI 的 base directory，不是前端源码目录：

```text
models/diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors
models/diffusion_models/Qwen-Image-2.1-Community-202610/qwen-image-2.1-UC-Q8_0.gguf
models/diffusion_models/Qwen-Image-2.1-Community-202610/qwen-image-2.1-UC-int8_convrot.safetensors
models/loras/Qwen-Image-2.1/qwen-image-2.1-uncensored-lora.safetensors
models/loras/Qwen-Image-2.1/qwen-image-2.1-consistency.safetensors
models/loras/Qwen-Image-2.1/qwen-image-2.1-restore.safetensors
```

原来的 `Qwen-Image-2.1-Uncensored/qwen-image-2.1-Q8_0.gguf` 没有覆盖或删除。基础 INT8、旧 Q8、编码器、VAE 的 SHA256 也已重新核对。

固定来源、大小和哈希集中在 `beginner_frontend/qwen21_updates.py`。前端下载管理复用现有安装器；以下维护命令仅下载或核验，不启动服务、不导入 torch：

```powershell
# 使用已安装 huggingface_hub 的 ComfyUI Python；替换两个路径占位符。
& '<ComfyUI Python路径>' -B scripts/prepare_qwen21_updates.py --base-dir '<ComfyUI base目录>'
# 只检查现有文件，不下载。
& '<ComfyUI Python路径>' -B scripts/prepare_qwen21_updates.py --base-dir '<ComfyUI base目录>' --verify-only
```

## 找到但未自动启用的更新

- **LightX2V Lightning 4 步**：真正的 2.1 专用蒸馏 LoRA，但作者要求动态指数时间偏移、指定 sigma 序列且无 terminal stretching，示例关闭 KV cache。不是把现有步数改为 4 就等价；本轮没有下载或冒充已完成适配。[作者说明](https://huggingface.co/lightx2v/Qwen-Image-2.1-Lightning)
- **ComfyUI 0.38 系列缓存/编译、Tiny VAE、Union ControlNet 更新**：涉及核心程序与依赖兼容，应单独升级并回归 H3/LTX。此次保持核心不动。[官方发行说明](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.38.0)
- **AtomicChat Turbo-Uncensored-GGUF**：仓库元数据的底模是 Qwen3-VL-8B，包含 mmproj，属于文本编码器路线，不能作为本平台的扩散模型替换项。[仓库元数据与说明](https://huggingface.co/AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF)
- **MLX、NVFP4、W4A8 及不同 Prompt Enhancer**：量化方式、推理后端和节点需求不同；没有因文件名类似就放入现有加载器，也没有替换原编码器。
- **旧 Qwen-Image / 2512 / Edit-2511 LoRA**：不当作 2.1 专用适配器加载。未明确底模和键结构的转载、提示词仓库没有安装。

## 来源与限制

- 官方于 10 月 9 日发布 Turbo，支持 8 步生成和编辑；本轮使用 Comfy-Org 的独立 INT8 权重，不是云 API。[Qwen 官方发布](https://github.com/QwenLM/Qwen-Image-2.1) · [Comfy 权重与采样配置](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)
- 原 abenzerps GGUF 仓库现指向 Uncensored-Quantized，新增 UC 命名的量化权重及 LoRA。Uncensored 是作者标注，不能据此推断所有内容都可生成或画质更好。[作者仓库](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized)
- Consistency 作者明确提醒可能压制姿态、转头等移动编辑；遇到此类需求应切回标准档。[Consistency](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA)
- Restore 作者建议模糊图片使用 0.5；它可能重绘小脸和细节，不是法证恢复或身份完全保真。本轮没有复制作者的自定义节点整套工作流。[Restore](https://huggingface.co/ausboss/Qwen-Image-2.1-Restore-LoRA)
- 基础模型及衍生文件须遵守对应许可；本平台没有因为社区仓库标签就将其统一视为 Apache 2.0。[Qwen 许可](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)

## 验证范围

- 6 个新文件及 4 个既有基础文件 SHA256 通过。
- 新档位的构图、加载器选择、参考图顺序、固定采样、LoRA 强度、离线/缺文件/缺节点门控通过 CPU 回归。
- 新旧 Q8 以及官方标准档互不覆盖；切换社区模式不修改共享配置。
- 前端在 1440px、390px 视口通过模型切换、参数锁定、微调档位和离线禁用测试。浏览器使用模拟 API、禁用 GPU，没有连接推理后端。
- 全量 `scripts/self_check.py --json` 通过，包括 144 项模型资产清单、17 张工作流图、H3/LTX 等 17 项回归、原 Qwen 9 项检查及新增 Qwen 检查。修正了临时目录下载测试误扫描真实模型盘的隔离问题。
- 未验证实际图像质量、速度、显存、GPU 执行与 Mac 推理效果。

created by zlk 2026
