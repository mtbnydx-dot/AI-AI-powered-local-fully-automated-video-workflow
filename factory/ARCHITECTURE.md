# 8×B200 AI 短剧工厂控制系统：架构与原理

本文对应 `factory/` 目录（分支 `feat/b200-control-system`）。它把对话中确定的路线落成代码：**云端 GPT 负责创作与出包，离线 B200 节点负责自动生成，本地 Qwen 负责审核与修复决策，控制平面负责状态、预算与安全，人类负责最终创作判断。**

它不是推理引擎、不是剪辑软件，也不是公网多租户平台。H3、FastH3、VDN-H3、Qwen 都由各自的推理服务运行，本系统只调度它们。

## 1. 拓扑

```text
云端工作室（可联网）                          离线机房（不依赖公网）
────────────────────                          ─────────────────────────────────────────────────
GPT：剧本 / 角色圣经 / 分镜 / 参考图            控制服务器（CPU）
  └ manifest.json + 素材                        ├ dfctl server：状态机 + HTTP API（单进程）
  └ dfctl package seal（ed25519 签名）          ├ PostgreSQL：全部状态、审核记录、事件审计
        │ 移动介质 / 受控交换网关               ├ 共享存储 /mnt/factory（或 MinIO）
        └──────────────────────────────────►   └ Prometheus（+ 各节点 DCGM exporter）
                                                       ▲ 节点用 HTTP 拉取任务（租约 + 续租）
                                                       │
                                                B200 节点 × N：dfctl agent（systemd）+ df-engines 容器
                                                ├ b200-01  GPU0-3 原生 Ref2VA｜GPU4-5 FastH3｜GPU6 VDN
                                                │          GPU7 MIG：Qwen3.8-27B / Qwen3-VL-8B / ASR / (CV)
                                                └ b200-02… 纯生成布局（不切 MIG）
        ◄──────────────────────────────────   签名结果包：成片、逐镜头审核记录、失败清单、成本指标
```

## 2. 关键设计决策

| 决策 | 选择 | 原因 |
| --- | --- | --- |
| 编排 | PostgreSQL 中的镜头状态机 + 租约任务队列，第一版不引入 Temporal | 离线机房少维护一个有状态集群；镜头流程是有界循环；全部状态在一个库里，备份、审计、断电恢复都简单。领域逻辑在 `policy/` 是纯函数，不绑定编排引擎 |
| 调度方式 | 节点**拉取**任务，带租约与续租 | 新节点发心跳即加入；节点宕机后租约到期，任务自动重排；控制服务不需要知道节点地址 |
| 审核 | **节点测量，中心判定** | 节点上测 ffprobe 指标、ASR 字错率、身份相似度；阈值来自任务包策略，在控制平面统一判定，换审核机器不改变标准 |
| 修复 | 有限动作词表 + 校验后执行 | 词表里根本没有“改剧情 / 发布”；导演 LLM 只能提议，`check_plan` 通过后才执行；每轮返工都是新版本，不覆盖旧结果 |
| 任务包 | 只含数据，不含命令 | 文件白名单 + 魔数识别 + 签名；引擎参数、命令、生成池能力都属于站点配置 |
| 显卡分配 | 节点 YAML 中的多个 profile，按批次切换 | 切换要排空、重载、预热，所以有持续时间、最小驻留和收益阈值，且一次只切一台节点 |

## 3. 任务包协议 epp/1

### 3.1 目录

```text
DRAMA_001_EP01_V3/
├── manifest.json            唯一的结构化数据：剧集、角色、场景、素材、镜头、审核/重试/自动化策略
├── characters/<id>/…        角色设定图（正脸/侧脸/全身）与声音参考
├── scenes/<id>/…            场景图
├── props/… shots/<id>/…     道具图、首尾帧
├── checksums.sha256         除自身和签名外每个文件的 SHA-256
└── signature.ed25519        对 checksums.sha256 的签名（JSON：algorithm / key_id / signature）
```

`dfctl schema` 输出 manifest 的 JSON Schema，直接交给云端 GPT 按格式生成；`factory/drama_factory/demo.py` 里的 `write_demo_package` 是一个完整示例。

### 3.2 镜头字段与 H3 规则

- **时长**：24 FPS，帧数必须是 `17n+5`（例：73、124、193、345、362）；只写 `duration_s` 时自动对齐。
- **模式**：`t2va`（无输入）、`fl2va`（首帧必填、尾帧可选、不接受参考）、`ref2va`（至少 1 个参考，不用首尾帧）。
- **参考上限**：图片 9、视频 3、音频 3、合计 12。提示词用 `<Picture n>` / `<Video n>` / `<Audio n>`，按类型分别编号，顺序即 `references` 列表顺序。
- **台词**：提示词中的 `<d>[语言] 台词</d>` 必须与 `dialogue` 列表逐句一致——ASR 审核按 `dialogue` 比对。
- **结构化提示词**：`description / action / camera / constraints / soundscape / music`，渲染为现有项目使用的 `integrated_multimodal_description / overall_soundscape / non_diegetic_music` 三段文本。拆字段是为了让自动修复只能改 `action / camera`、只能追加 `constraints`。
- **画布**：768p 基础画布（9:16 为 768×1344）。1080×1920 交付属于后期超分，不是 H3 输出。
- **其他**：`importance: key` 触发 L3 与人工终审；`motion / interaction / dialogue_precision` 参与难度与路由；`depends_on` 控制先后；`props` 供连续性修复使用。

### 3.3 封装与校验

云端：`dfctl package check DIR` → `dfctl package seal DIR --key studio.key --tar DIR.tar`。

站点导入（`dfctl import` → `POST /v1/packages/import`）先把包复制到控制服务私有的 `staging_dir`（不跟随链接、复制前后核对文件身份），之后所有校验和入库都只读这份副本，导入结束即删除。这样能写 inbox 的一方（交换网关、移动介质、共享目录）无法在“校验通过”和“入库”之间调包。然后按顺序检查：

1. 路径必须位于 `site.import_roots` 内；tar 只能含普通文件和目录，拒绝 `..`、绝对路径、链接。
2. 签名者必须在 `trusted_keys_dir` 中；签名覆盖 `checksums.sha256`。
3. 实际文件集合与 checksums **完全一致**（多一个即视为夹带），逐个核对哈希。
4. 只允许图片、视频、音频、JSON/文本；媒体按魔数识别，扩展名与内容不符即拒绝。
5. manifest 严格 Schema（未知字段即报错），再做语义检查：引用存在、模式与输入匹配、参考上限、提示词标记、台词一致、依赖无环、站点生成池支持该模式与帧数。
6. 许可地区：`distribution.regions` 不得包含 `license.restricted_regions`（默认 US/EU/UK/KR）中未登记授权的地区；`Global` 视为包含全部。

同一 `package_id` 重复导入：内容相同则幂等，内容不同则拒绝——修改必须提升 version 并换新 ID。

## 4. 镜头状态机

```text
blocked ─(依赖已通过机器审核)→ generating ─→ reviewing ─┬─ 通过 ─→ approved
                                  ▲                     │        └→ needs_human(final_check / uncertain) → approved / rejected
                                  │                     └─ 本轮全部失败 ─→ (repairing：导演 LLM) ─→ 新一轮
                                  └──────────── 修复计划 ◄──────────────┘          └→ needs_human(预算用尽 / 无权修复 / 建议拆镜头)
```

- **轮次与候选**：每轮按预算生成 N 个候选（不同种子），默认第一个通过的胜出，其余作废；`candidate_selection: best_score` 时等全轮结束择优。
- **技术重试**（OOM、超时、节点失联、服务报错）：同一任务指数退避重排，不消耗创作预算；用尽后该候选记为 error，整轮都是 error 时换更大的生成池或转人工。每次尝试写入独立的输出键（`…-a<次数>.mp4`），控制平面只接受本次租约对应的键；与控制平面失联、租约已被收回的节点即使晚些跑完，也覆盖不了后来被批准的文件。节点上传前还会再确认一次租约。
- **创作重试**：每轮必须改变参考、提示词约束、生成池或模式，不允许原样换种子无限重抽。
- **预算**（可在包内覆盖）：

| 难度 | 最多轮次 | 每轮候选 | 最多候选总数 | 单任务技术重试 |
| --- | --- | --- | --- | --- |
| simple | 2 | 2 | 4 | 3 |
| normal | 3 | 1 | 4 | 3 |
| hard | 4 | 1 | 5 | 3 |

- **可追溯**：每个 take 保存完整生成配方（模式、渲染后的提示词、参考与编号、帧数、种子、生成池、模型版本）、L0 指标、全部审核报告、修复计划；事件表记录每次状态变化。种子由（包、镜头、轮次、候选序号）确定，可复现。

## 5. 路由

难度分 = 角色数（≥3 人额外加分）+ 动作幅度 + 肢体互动 + 精确台词 + 台词条数 + 时长（>10 秒）。

| 目标层级 | 条件 | 生成池 |
| --- | --- | --- |
| L1 速度优先 | 无人物，或单人、低动作、无精确台词的 T2VA/FL2VA | `vdn`（VDN-H3） |
| L2 参考优先 | Ref2VA、两人、精确台词 | `fast_ref`（FastH3 Omni） |
| L3 质量优先 | ≥3 人、高强度肢体互动、高难度关键镜头 | `native_ref`（原生 H3 Ref2VA） |

能力约束优先于层级：VDN 不做 Ref2VA；帧数超过某池上限会跳到更高的池；`automation.max_route` 限制自动升级的上限。

## 6. 审核

| 级别 | 在哪里跑 | 做什么 | 阈值 / 判定 |
| --- | --- | --- | --- |
| L0 | 生成节点 CPU（ffprobe + ffmpeg） | 可解码、分辨率、帧率、时长、黑场、冻结、音轨、削波、有台词却静音 | 控制平面按 `review_policy` 判定 |
| L1 | GPU7 MIG 2g.45gb：Qwen3-VL-8B | 抽帧（默认 1 fps + 尾帧）对照角色参考图检查身份、服装、场景、动作、肢体、穿帮、文字 | 必须给出定位到秒的问题码；“不通过但说不出问题”按不确定处理 |
| L2.asr | GPU7 MIG 1g.23gb：Qwen3-ASR | 转写音轨，与 `dialogue` 算字错率（中英文统一按字计） | `max_dialogue_cer`（默认 15%） |
| L2.identity | P2 接入：有商业授权的人脸/人物特征服务 | 各角色与设定图的相似度 | `min_identity_similarity`（需人工标注校准） |
| L3 | GPU7 MIG 3g.90gb：Qwen3.8-27B | 关键镜头、不确定、抽样；附上一个已通过镜头的结尾帧检查跨镜头连续性 | 同 L1 |
| 人工 | 导演 | 关键镜头终审、不确定的镜头、所有升级任务；整集拼接后完整观看 | — |

- L1 与 L2 并行；任一出现阻断级问题立即判失败并取消其余审核（省 GPU）。
- “不确定”按来源处理：L1 / 身份比对的疑问可以由 L3（看画面）解决；台词疑问（ASR 宕机、或返回结果却没有字错率）L3 解决不了，一律交人工试听。测量类审核缺测量值按不确定处理，不算通过；身份比对必须覆盖本镜头每个角色，缺一个即判“未检测到”。
- `site.review_levels` 声明本站实际部署了哪些级别：未部署的级别会被跳过并记录事件，**绝不伪造通过**；缺 L3 时，需要 L3 的镜头转人工。
- 审核模型的数字评分不能直接当概率，阈值必须用人工标注集校准（P2 验收项）。

## 7. 自动返工

失败原因按优先级归类：配置 > 剧情 > 身份 > 连续性 > 台词 > 动作 > 画面瑕疵 > 技术。同类问题在之前轮次出现的次数决定下一步：

| 类别 | 第一次 | 再次出现 | 第三次 |
| --- | --- | --- | --- |
| 配置（分辨率/帧率不符） | 直接转人工（重试无效），同时计入生成池失败连击 | — | — |
| 剧情偏离 | 换种子 + 强调剧本顺序 | 转人工（无权改剧情） | — |
| 身份 / 服装漂移 | FL2VA→Ref2VA 升级（首帧变 `<Picture 1>`，加锁定设定图）或补参考 | 升级生成池 | 转人工 |
| 道具 / 场景不连续 | 补道具、场景参考 | >6 秒的镜头建议拆成两段（交人工确认） | 转人工 |
| 台词 / 口型 | 换种子 + 补说话人声音参考 + 只说剧本台词 | 升级生成池 | 转人工，建议配音 + 口型同步 |
| 动作 / 肢体 | 换种子 + 放慢动作 | 升级生成池 | 建议拆镜头或转人工 |
| 画面瑕疵 | 换种子（文字类加“不出现文字”约束） | 换种子 | 升级生成池 |

新加入的参考会自动追加说明，例如 `<Picture 2> is the front face reference for Lin Qiao.`，已有编号保持不变。

任何修复（规则、导演、人工）执行后，提示词里的 `<d>` 台词必须与剧本逐字、逐句、同序一致，否则方案作废——台词只能通过新版本任务包修改。规则方案同样遵守 `editable_prompt_fields`：不允许改 `action` 时，“放慢动作”改为追加一条约束。

**本地导演（`automation.director: llm`）**：Qwen3.8 收到镜头、当前配方、问题、历史、剩余预算、可用素材和规则引擎的建议方案，返回 RepairPlan JSON。控制平面校验后执行，不通过则记录 `plan_rejected` 并回退到规则方案。导演服务不在线时直接用规则方案；请求发出后超过 `scheduler.director_wait_s`（默认 600 秒）无人领取，也回退到规则方案。

| 对话中的工具 | 本系统动作 | 自动执行边界 |
| --- | --- | --- |
| inspect_shot / compare_references | 审核任务本身 | 允许 |
| revise_prompt | `revise_prompt` | 只能改 `action`、`camera`，只能追加 `constraints`；不得加入台词标签或不存在的参考编号 |
| switch_model | `switch_route` / `upgrade_mode` | 不超过 `automation.max_route`，目标池必须支持该模式和帧数 |
| — | `add_references` | 只能加**已锁定**、属于本镜头角色/场景/道具的素材，且不超参考上限 |
| split_shot | `propose_split` | 只产生拆分建议，进入人工队列 |
| retry_generation | 新一轮 | 受轮次与候选总数预算约束 |
| change_story | 不存在 | 不可能执行 |
| publish_episode | 不存在 | 结果只导出，由人发布 |

人工处理（`dfctl decide`）：`approve`（可指定任一已生成的 take）、`reject`、`retry`（可附 RepairPlan，可追加轮次；人工方案可以改 `description`，但仍校验素材、参考与台词）。`reject` 会清除该镜头的选中 take，它不再用于 L3 连续性对照或候选导出；仍在等待它的下游镜头记录 `dependency_rejected` 事件，并在结果包里列出 `blocked_by`，由云端出新版本处理。

## 8. GPU 布局与 profile

`config/node-b200-01.example.yaml` 是对话的最终方案：GPU0-3 原生 Ref2VA（4 卡），GPU4/5 FastH3 Omni，GPU6 VDN-H3，GPU7 按 all-balanced 切 MIG：3g.90gb（Qwen3.8-27B，L3 + 导演）、2g.45gb（Qwen3-VL-8B，L1）、1g.23gb（ASR）、1g.23gb（预留身份比对）。另提供 `ref_heavy`、`vdn_heavy` 两种保留 GPU7 的批量布局。`node-b200-02.example.yaml` 是第二台起的纯生成节点（方案 A / C / 双原生）。

`dfctl node check` 会拦截：同一设备分给两个实例、MIG 切分超出 7 计算 / 8 显存切片、已切 MIG 的卡被整卡使用、实例卡数与模型要求不符、显存需求超过分配（例如 Flash-Next NVFP4 约 130GB 放不进 90GB MIG）、端口冲突。未通过 P0 的模型会给出提醒。

`dfctl node render` 为每个 profile 生成 docker compose（`df-engines` 项目）：`NVIDIA_VISIBLE_DEVICES` 指定整卡或 MIG 设备（优先用 `mig_uuids` 中的 UUID），共享存储按原路径挂载，带健康检查。

切换流程：控制平面设定 `desired_profile` → 节点停止领取新任务、等在跑的任务结束 → 执行本机 `switch_command` → 等推理服务健康 → 心跳上报新 profile → 恢复领取。自动规划（`scheduler.auto_switch_profiles`，默认关）按“清空当前积压所需时间”比较各 profile，只有收益超过 `switch_gain`、条件持续 `switch_sustain_s`、距上次切换超过 `switch_min_dwell_s` 才切，且同一时刻只切一台。

节点在 `work_dir/agent-state.json` 记录**实际运行**的 profile，切换前写入“正在切换到 X”。切换命令失败或超时，节点保持排空并退避重试，不会退出；节点代理中途崩溃重启后，先把未完成的切换做完再领任务，并以该 profile 上报，控制平面随之对齐。因此 `node.yaml` 里的 `profile` 只是首次启动的默认值，之后请用 `dfctl profile` 切换。

## 9. 无人值守与安全联锁

| 告警 | 触发 | 动作 | 解除 |
| --- | --- | --- | --- |
| GPU_HOT / GPU_ECC | 温度 ≥ 87°C，或出现不可纠正 ECC | 节点排空（不再领新任务） | `dfctl resume-node` |
| DISK_LOW | 任一节点关键目录剩余 < 500GB | 全站暂停领取生成任务（审核继续，把在途结果做完） | `dfctl resume` |
| FAILURE_STREAK | 某生成池连续 6 条技术性失败 | 暂停该池（疑似模型或服务异常） | `dfctl resume` |
| REVIEW_BACKLOG | 待审核 ≥ 200 | 暂停生成，降到 ≤ 50 自动恢复 | 自动 |
| NODE_SILENT | 心跳超时 | 标记离线；租约到期后任务重排 | 心跳恢复 |
| POOL_NO_CAPACITY | 有任务排队但没有在线节点提供该池 | 告警 | 调整 profile 或 review_levels |

- **断电 / 重启**：所有状态在数据库，控制服务重启后继续；节点失联的任务在租约到期后自动重排；已通过的镜头不会重做。
- **定时维护互不牵连**：每个过期租约、导演超时、看门狗、规划器各自在独立事务里执行；某一项出错（例如节点上报了异常遥测）只记录在 `dfctl status` 的 `tick.errors` 里，不会让其他维护停摆。遥测里的非数字一律按“未知”处理。
- **领取任务不重试读超时**：领取请求在服务端已成功、只是响应丢失时，节点不再重复领取，避免孤儿租约白白消耗重试次数。
- **迟到的结果**：租约失效后提交的结果返回 409 并被丢弃，不会重复推进状态。
- **离线**：运行期间不访问公网；镜像、权重、分词器、VAE 必须提前进入本地仓库和 `/models`。

## 10. 多节点

- 新节点：复制 `node-b200-02.example.yaml`、改 `node_id`、生成并启动 compose、启动 agent，心跳到达即加入，控制服务配置不用改。
- 所有节点必须挂载同一共享存储（`store.kind: fs`），或改用 MinIO（`store.kind: s3`，节点本地缓存）。
- 只在机房内网开放 8700 端口。第一版所有节点共用一个 worker token；按节点签发 token 列在后续工作中。

## 11. 结果回传

`dfctl export <package>` 生成并用站点私钥签名的结果包：

```text
DRAMA_001_EP01_V3-results-<时间>/
├── results_manifest.json    每个镜头的状态、选中 take、生成池、模型版本、种子、未决人工任务、最后一轮问题
├── media/<shot>.mp4         已批准的镜头（--media candidates 时含机器通过、待终审的）
├── reviews/<shot>.json      全部 take、审核报告、修复计划、事件
├── checksums.sha256
└── signature.ed25519
```

汇总指标包含**每分钟合格成片 GPU 小时**（全部 take 的 GPU 秒 ÷ 已批准成片分钟数，MIG 按算力份额折算）、一轮通过率、平均候选数。云端用站点公钥校验后交给 GPT 或导演安排下一轮。`license_notice` 记录了 H3 输出不得用于训练非 H3 模型等限制。

## 12. 实施阶段与本分支进度

| 阶段 | 本分支状态 | 上真机后要做的事 |
| --- | --- | --- |
| P0 模型基准 | 未做，需要真机 | 固定镜像与启动参数；用 100–200 个不同难度镜头测速度、显存峰值和质量；按引擎实际接口调整 `HttpVideoBackend` 的 paths / fields；校准 `vram_gb`、`max_frames`；通过后把 `verified` 改为 true |
| P1 单镜头自动化 | 已实现：任务包、路由、候选、技术/创作重试、租约、断点恢复 | 用真实引擎跑通 |
| P2 自动审核 | L0、L1/L3（OpenAI 兼容 VLM）、ASR 客户端与中心判定已实现 | 部署身份比对服务；用人工标注校准阈值与误判率 |
| P3 剧集生产 | 依赖、L3 跨镜头连续性、人工队列、结果包已实现 | 整集拼接后的连续性审核、剪辑交付链路 |
| P4 吞吐优化 | profile 规划器已实现（默认关闭） | 积累运行数据后开启；按镜头类型调参 |

## 13. 已知限制与待验证项

- H3 系列引擎（SGLang Diffusion、FastVideo、vLLM-Omni）的 HTTP 字段名尚未在真机验证，全部集中在 `HttpVideoBackend` 的配置项里。
- Qwen3.8、Qwen3-ASR 等模型名与服务方式沿用对话中的方案，需按实际发布版本核对；ASR 走 OpenAI 兼容的 `/v1/audio/transcriptions`。
- B200 MIG 规格表按 NVIDIA MIG 文档整理，上线前用 `nvidia-smi mig -lgip` 核对。
- 数据库表结构目前由 `create_all` 建立；第一次生产升级前需要引入 Alembic 迁移。
- 控制服务是单进程：高可用依赖 PostgreSQL 与自动重启，不支持多活。
- 拒绝镜头后，已经开始的下游镜头不会自动回滚，在结果包中可见，由导演决定。
- 许可地区检查只是导入闸门，不构成法律意见；肖像、声音、版权授权需另行确认。

## 14. 代码地图

| 路径 | 内容 |
| --- | --- |
| `drama_factory/protocol/` | `h3.py` 时长/画布/标记规则；`manifest.py` 任务包模型与语义检查；`package.py` 封装、校验、tar 安全解包；`keys.py` Ed25519 |
| `drama_factory/policy/` | 纯函数：`routing.py` 难度与路由；`review.py` L0 规则与审核判定；`repair.py` 修复规则、导演方案校验、方案应用；`spec.py` 生成配方与种子；`taxonomy.py` 问题码 |
| `drama_factory/control/` | `controller.py` 状态机；`api.py` HTTP 接口；`watchdog.py` 安全联锁；`export.py` 结果包 |
| `drama_factory/node/` | `agent.py` 节点代理与租约；`backends.py` 引擎与审核客户端；`probe.py` L0 测量；`telemetry.py` GPU/磁盘 |
| `drama_factory/cluster/` | `config.py` 站点/节点配置；`profiles.py` 布局校验与 compose 生成；`planner.py` 切换规划 |
| `drama_factory/store/` | 数据表与会话 |
| `drama_factory/demo.py` | 单机模拟全流程 |
| `config/`、`deploy/` | 示例配置、控制服务 compose、节点 systemd 单元、DCGM |
| `tests/` | 协议、封装安全、策略、布局、联锁、全流程、节点适配的自动化测试 |
