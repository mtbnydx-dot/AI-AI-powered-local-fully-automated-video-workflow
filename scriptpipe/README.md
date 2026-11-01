# scriptpipe：长篇小说到文字剧本包实验

这是独立于 `videopipe` 的上游文字创作模块。实际调用指定的 GLM API；本模块不生成图片、音频或视频。

当前创作模板针对本次《蒋兴哥重会珍珠衫》实验；更换小说时需同步调整模板中的原作事件要求，而不只是替换文件路径。

## 已压缩的创作上下文

- 当前分支 `kk-8xb200`；下游面向八卡 B200。
- 整季共用人物、服装、场景、关键道具设定；逐集保存起止状态和知情范围。
- 中文剧本和对白；H3 画面、声音、首帧生成说明用英文。
- 每镜完整完成一个小动作，切镜发生在稳定结果之后，不拆分同一人物的一段动作。
- 每镜最多 6 秒；默认 5.875 秒＝141 帧，符合当前 H3 的 `5 + 17n` 帧格。直接请求 6 秒会补至 158 帧，超过约束。
- 单镜优先一位说话人、18 个以内中文口语字，留反应和动作时间。字速检查是估算，不是实际配音证明。
- 图片只预留槽位，绑定保持 I2VA 意图；补齐图片之前不直接运行视频 CLI。
- 不增加审批、鉴权框架或内容哈希。状态与阶段结果落盘，重跑可复用已完成内容。

## 最小流程

1. 读取原文，去掉采集混入的 MediaWiki CSS，原文件不变。
2. GLM 规划完整季设定、角色与资产表、分集事件和起止状态。
3. 按固定季设定并发生成各集文字分镜。
4. 检查字段、引用、时长、对白预算；GLM 检查全季因果、身份、动作闭合和道具流转。
5. 只替换有明确错误的镜头，必要时调整镜头顺序；未涉及镜头保持原文，保留旧稿与审核结果。
6. 确定性编译 H3 prompt、可读剧本、素材槽位和当前下游兼容的 package。

## 运行

在仓库根目录，使用已安装项目依赖的 Python 环境。`.env` 提供 `GLMKEY`（支持等号两侧空格），不会打印或复制到产物。

```bash
.venv/bin/python -m scriptpipe \
  --source 'materials/novel_research_2026-10-31/texts/01_蒋兴哥重会珍珠衫_原文.txt' \
  --notes scriptpipe/source_notes.md \
  --output runs/scriptpipe/pearl_shirt_20261031 \
  --episodes 12 --concurrency 4 --repair-rounds 2
```

默认 endpoint 为 `https://ark.cn-beijing.volces.com/api/coding/v3`，模型为 `glm-5.3`。保留思考模式：全季规划默认 `high`，分集写作、修复与整季审核默认 `low`。本次服务默认 `max` 的规划耗尽32768 tokens仍无正文；`high`整季审核又在约768秒后返回服务内部错误，未给出结论，因此按阶段控制推理强度。`--reasoning-effort` 调整规划档位，`--review-reasoning-effort` 单独调整审核档位。

使用流式返回以容纳长请求。保存请求文本、最终回答、调用时间和 token 用量；不保存密钥或模型思考正文。初始输出预算 32768 tokens，截断时提高预算重试；按45秒间隔记录接收进度。

复用同一输出目录即从已有季设定与各集草稿继续；修改原文、创作要求或 prompt 后应选新目录，避免复用旧稿。本次草稿也会随修正保留历史版本。

## 文件职责

- `llm.py`：环境变量读取、流式 API、有限重试与调用记录。
- `pipeline.py`：季规划、并发创作、全季审核、定向修正。
- `checks.py`：实际与 H3 对齐后的时长、对白和引用等文本检查。
- `export.py`：H3 prompt、剧本包和图片槽位导出。
- `prompt/scriptpipe/*.txt`：创作、审核、修正及 H3 编译模板。
- `source_notes.md`：本次小说的人工研究参考，只有通过 `--notes` 才传入模型。

`delivery/episodes/<集ID>/package.json` 符合现有 `videopipe` 的结构，含尚未生成的首帧与审核图片路径。`image_slots.json` 说明未来需要生成的图片、英文提示和一致性来源。`quality.json` 如实记录未解决问题；文字检查通过不代表实际视频质量已验证。

## 一小时以上的长版

长版复用相同客户端、镜头格式和导出模块，按“整季规划 → 分集场景计划 → 分场并发写作 → 逐集与全季连续性检查”生成；不复用旧短版内容。默认8集，每集7场、每场12镜、每镜5.875秒，共672镜、65分48秒。

```bash
.venv/bin/python -m scriptpipe --long-form \
  --source 'materials/novel_research_2026-10-31/texts/01_蒋兴哥重会珍珠衫_原文.txt' \
  --notes runs/scriptpipe/pearl_shirt_long_20261031/creation_notes.md \
  --output runs/scriptpipe/pearl_shirt_long_20261031 \
  --delivery examples/pearl_shirt_long \
  --episodes 8 --concurrency 8 --repair-rounds 1 --reasoning-effort low
```

`long_pipeline.py` 管理分场写作和恢复；`long_checks.py` 检查分配镜号、完整场景、整季时长；`prompt/scriptpipe/long_*.txt` 是对应模板。`--stop-after season|plans|drafts|review` 只在指定阶段落盘退出，适合本地查看中间结果；不指定就继续到导出。运行记录留在 `runs/`，最终文本剧本放到指定 `--delivery` 目录。图片仍为待补槽位。
