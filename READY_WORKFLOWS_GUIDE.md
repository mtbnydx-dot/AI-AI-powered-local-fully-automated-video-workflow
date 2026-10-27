# ComfyUI 手工参考工作流说明

## 工作流位置

项目内文件位于：`workflows/ready/`。

可以直接拖入 ComfyUI 页面，或复制到 `<COMFYUI_USER_DIR>/default/workflows/` 后从工作流菜单打开。

如果 ComfyUI 页面没立刻显示，刷新浏览器页面即可。

日常使用优先从前端模型控制台选择 MiniMax H3、LTX-Video、HunyuanVideo 或 Wan2.2；前端会按当前模型动态构建并预检 API workflow。本文件列出的是可拖入 ComfyUI 的手工模板，编号不代表全局推荐顺序。

## 1. Wan2.2 I2V-A14B 手工模板

打开：

`01_Wan22_I2V_A14B_720p_4step_READY.json`

用途：

用关键帧控制构图、人物、场景和品牌风格，生成最终 720P 镜头。

已配置：

- 尺寸：`1280x704`
- 帧数：`81`
- FPS：`24`
- high noise：`Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`
- low noise：`Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors`
- VAE：`wan_2.1_vae.safetensors`
- CLIP：`umt5_xxl_fp8_e4m3fn_scaled.safetensors`
- Lightx2v 4-step LoRA 已接好
- 示例关键帧：`wan22_sample_esports_keyframe.png`

你只需要改：

- `Load Image`：换成自己的关键帧
- `Positive Prompt`：写镜头动作和画面描述
- 想竖屏就把宽高改成 `704x1280`

## 2. Wan2.2 T2V-A14B 手工模板

打开：

`02_Wan22_T2V_A14B_720p_4step_READY.json`

用途：

没有参考图时做概念探索。它可以直接 Queue，但一致性不如 I2V。

已配置：

- 尺寸：`1280x704`
- 帧数：`81`
- FPS：`24`
- T2V high/low noise A14B fp8
- T2V Lightx2v 4-step LoRA

你只需要改：

- 主提示词
- 随机种子，固定 seed 可复现，randomize 可探索

## 3. Wan2.2 TI2V-5B 手工模板

打开：

`03_Wan22_TI2V_5B_720p_DRAFT_READY.json`

用途：

快速试动作、镜头运动、prompt 方向。满意后可以沿用该模型，也可以回到前端切换任意高质量模型。

已配置：

- 模型：`Wan2.2/wan2.2_ti2v_5B_fp16.safetensors`
- VAE：`wan2.2_vae.safetensors`
- 尺寸：`1280x704`
- 帧数：`81`
- FPS：`24`
- 默认接入同一张示例关键帧

建议：

- 草稿阶段先用 `20` steps。
- 如果画面不稳，先别急着加步数，优先改 prompt 和关键帧。

## 4. 后处理插帧：RIFE 2x

打开：

`04_RIFE_2x_Postprocess_READY.json`

用途：

把生成好的视频做 2x 插帧并输出 MP4。

已配置：

- RIFE：`rife49.pth`
- 插帧倍数：`2`
- 输出 FPS：`48`
- 输出格式：`video/h264-mp4`
- CRF：`19`

你只需要改：

- `Load Generated Clip`：选择要后处理的视频
- 如果原片是 16fps，把输出 frame_rate 改成 `32`
- 如果原片是 24fps，保持 `48`

## 5. MiniMax H3 FL2VA：文字生原生音视频

打开：`05_MiniMax_H3_FL2VA_T2V_INT8_READY.json`

适合没有参考图时直接生成画面、对白、音效和环境声。模型固定 24fps；首次建议 864x480、73 帧、20 步。提示词里同时写清楚画面、镜头运动、说话人、对白内容和环境声。

## 6. MiniMax H3 FL2VA：首帧/首尾帧生原生音视频

打开：`06_MiniMax_H3_FL2VA_I2V_INT8_READY.json`

首帧用于锁定人物与构图，尾帧可选，用于控制镜头终点。四个模型文件必须放在 ComfyUI 对应的 `models/diffusion_models`、`models/text_encoders` 和 `models/vae` 目录。模型总下载约 53.9GB，要求 ComfyUI 0.30.1 或更新版本。

## 推荐拍片顺序

1. 在前端模型控制台按需要选择能力：是否要关键帧、尾帧或原生音频。
2. 用当前模型的低分辨率短镜头参数快速试动作。
3. 沿用当前模型或切换同家族质量档，生成 3-6 秒正式镜头。
4. 需要时做闪烁修复、RIFE 插帧和超分。
5. 多个镜头进剪映/达芬奇拼接、调色、加音乐。

## 核查

运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_wan22_setup.ps1
```

看到 `Local AI video workbench setup verified.` 就说明脚本已完成基础模型和节点检查。前端里的 `生成链路测试` 才会验证当前所选模型能否真实生成。
