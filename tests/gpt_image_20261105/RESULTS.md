# GPT Image 实测结果

2026-11-05，使用用户指定的 `OPENAI` 凭据直连官方 Image API。三个请求均返回 HTTP 200。模型 `gpt-image-2.6-sunburst`，`quality=max`，原图 1152×2048 PNG，`complexity` 使用默认值。

| 任务 | 参考图数 | API 耗时 | 文字输入 token | 图像输入 token | 图像输出 token | 费用估算 USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 角色身份母图 | 0 | 79.326 秒 | 245 | 0 | 5,650 | $0.170725 |
| 从母图换装 | 1 | 69.381 秒 | 268 | 1,508 | 5,650 | $0.182904 |
| 双人场景首帧 | 5 | 87.129 秒 | 700 | 5,776 | 5,650 | $0.219208 |
| 合计 | | 235.836 秒 | 1,213 | 7,284 | 16,950 | **$0.572837** |

费用由响应 `usage` 和官方 Standard 费率计算，包含文字输入、参考图输入和图片输出；平台账单是最终依据。三个单样本的耗时代表本次调用。[费率来源](https://developers.openai.com/api/docs/pricing)

本实验复用现有 `imagepipe.plan.compile_plan` 生成的英文 prompt，将编译画布设为原生 1152×2048。第一张图片产生角色身份，第二张以上一张原图为输入。第三张的输入顺序为：

1. 本次新生成人物身份母图（1152×2048）。
2. 本次新生成该人物的服装参考（1152×2048）。
3. 既有项目另一人物的身份母图（720×1280）。
4. 既有项目该人物的黑西装参考（720×1280）。
5. 既有项目酒店房间参考（720×1280）。

实际文件顺序和路径位于 `03_first_frame/request.json`；素材来源位于 `manifest.json`。

**目视检查：**

- 身份母图：一个完整头部及肩部，面部皮肤、眼睛和发丝细节清楚，背景简单、光线均匀，符合身份参考用途。
- 换装图：面部和发型主观上延续母图；白衬衫、黑色及膝裙、深色薄丝袜和黑鞋得到呈现；人物从头到鞋完整入画。
- 首帧：两个人物分别位于画面右侧和左侧；脸与服装的对应关系得到保留。沙发、窗户、书桌、门和设备的主要位置延续场景参考，色温与目标夜景一致。纸摞由人物双手持于设备上方，符合所要求的动作开始状态。

三张图均为单一连续画面。检查基于当前样本的目视观察；跨镜头身份稳定性、复杂遮挡和持续编辑后的细节保留，适合在后续迁移的连续场景中验证。此次没有对其他 GPT Image 模型生成相同测试组，因此这里提供接入可行性与质量样本，模型间排名仍待对照实验。

原图通过既有 `imagepipe.delivery.deliver_image` 生成 720×1280 RGB PNG。三个 `crop_box` 都是 `[0, 0, 1152, 2048]`，即完整保留原图画面再缩放。后续生成可继续读取保存的原生参考图，视频交付使用 720×1280 文件。

已验证的实际行为：

- 纯文本 JSON 请求 `/v1/images/generations`。
- 单图和五图 multipart 请求 `/v1/images/edits`，使用重复 `image[]`。
- 2.6 Sunburst 接受 `max` 和 1152×2048，并返回相符的质量与尺寸元数据。
- 按参考图依赖顺序传递本次新生成的原图，解码 `b64_json`，保存 `usage` 与 `x-request-id`。
- 复用生产交付函数生成目标图片。生产目录与配置保持原状。

API 的 16 张参考图上限、32,000 字符 prompt 上限和 `n=1..10` 来自官方文档，本轮仅实际生成三张图片。完整生产调度器的执行、恢复及并发行为留在后续正式接入范围。

角色母图：

![角色身份母图](/home/jzxu/code/AI-AI-powered-local-fully-automated-video-workflow/tests/gpt_image_20261105/01_identity/delivered.png)

单图换装：

![从母图换装](/home/jzxu/code/AI-AI-powered-local-fully-automated-video-workflow/tests/gpt_image_20261105/02_wardrobe/delivered.png)

五图合成首帧：

![五图合成首帧](/home/jzxu/code/AI-AI-powered-local-fully-automated-video-workflow/tests/gpt_image_20261105/03_first_frame/delivered.png)
