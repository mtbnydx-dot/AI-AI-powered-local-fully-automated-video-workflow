# 短剧工厂控制系统（8×B200 离线集群）

把 MiniMax H3 系列模型变成可以连续几天无人值守的 AI 短剧生产线：云端 GPT 出签名任务包，离线 B200 节点自动生成、分级审核、按规则或本地导演模型返工，超出预算或需要创作判断的镜头进入人工队列，最后导出签名结果包。支持一台或多台 8×B200 节点。

设计原理、状态机、审核与返工规则见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 能做什么

- **任务包协议 epp/1**：角色圣经、场景、参考素材、结构化镜头、审核/重试/自动化策略；Ed25519 签名、文件白名单、H3 规则校验（17n+5 帧、Ref2VA 9/3/3/12 参考上限、`<Picture n>` 与 `<d>[语言] 台词</d>` 一致性）、许可地区闸门。
- **路由**：按角色数、动作、互动、台词精度把镜头分到 VDN-H3 / FastH3 Omni / 原生 H3 Ref2VA，失败时逐级升级。
- **审核**：L0 机器检测（ffprobe/ffmpeg）→ L1 快速 VLM + L2 台词字错率 / 身份相似度 → L3 深度审核 → 人工终审；阈值由控制平面统一判定。
- **自动返工**：有限动作词表（补锁定参考、FL2VA→Ref2VA、收紧约束、升级生成池、建议拆镜头），导演 LLM 的方案先校验再执行，预算用尽转人工。
- **多节点调度**：节点拉取任务、租约续租、断点恢复；GPU/MIG profile 校验、compose 生成、带滞后阈值的 profile 切换。
- **无人值守联锁**：GPU 过热/ECC 排空节点，磁盘不足全站暂停，连续技术失败暂停生成池，审核积压自动限流。
- **结果回传**：成片、逐镜头审核记录、失败清单、每分钟合格成片 GPU 小时，用站点私钥签名。

## 先在本机跑一遍（不需要 GPU）

```bash
cd factory
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"
```

```bash
.venv/bin/dfctl demo --workdir ./demo-run
```

Windows 下把 `.venv/bin/` 换成 `.venv\Scripts\`。演示会封装一个 5 镜头的示例包，启动控制平面和一台模拟的 8×B200 节点（有 ffmpeg 时渲染真实测试片），依次走过：OOM 技术重试、身份漂移自动升级 Ref2VA、关键镜头 L3 + 人工终审、台词三轮失败后转人工、依赖镜头等待，最后导出签名结果包。

```bash
.venv/bin/python -m pytest -q
```

## 上线步骤

1. **两对密钥**：云端 `dfctl keys keys/studio`，站点 `dfctl keys keys/site`；互相把 `.pub` 放进对方的 trusted_keys。
2. **控制服务器**（CPU 机）：按 [config/site.example.yaml](config/site.example.yaml) 写 `/etc/drama-factory/site.yaml`，设置 `DF_DB_PASSWORD / DF_WORKER_TOKEN / DF_OPERATOR_TOKEN`，`docker compose -f deploy/control/docker-compose.yml up -d`。
3. **每台 B200 节点**：
   - 按 [config/node-b200-01.example.yaml](config/node-b200-01.example.yaml)（带 MIG 审核）或 [node-b200-02](config/node-b200-02.example.yaml)（纯生成）写 `/etc/drama-factory/node.yaml`；
   - `dfctl node check --node node.yaml` 校验布局；GPU7 用 `nvidia-smi mig` 切好后把 `nvidia-smi -L` 的 MIG UUID 填进 `mig_uuids`；
   - `dfctl node render --node node.yaml` 生成各 profile 的 compose，`docker compose -p df-engines -f compose/<profile>.yml up -d` 启动推理服务；
   - 安装 [deploy/node/drama-factory-agent.service](deploy/node/drama-factory-agent.service) 与 DCGM exporter。
4. **P0 基准**：先拿 100–200 个真实镜头验证各引擎的接口字段、显存峰值、速度与质量，再把对应模型的 `verified` 改为 true（详见 ARCHITECTURE.md 第 12 节）。

所有镜像、模型权重、分词器和 VAE 都要提前放进本地仓库与 `/models`，运行期间不访问公网。

## 云端出包

```bash
dfctl schema --out epp.schema.json
```

把 Schema 和剧本交给 GPT 生成 `manifest.json` 与参考图，人工锁定角色素材后：

```bash
dfctl package check DRAMA_001_EP01_V1 --site site.yaml
```

```bash
dfctl package seal DRAMA_001_EP01_V1 --key keys/studio.key --tar DRAMA_001_EP01_V1.tar
```

## 运维速查

控制服务地址与令牌用环境变量 `DF_CONTROL_URL`、`DF_OPERATOR_TOKEN` 提供。

| 命令 | 作用 |
| --- | --- |
| `dfctl import /mnt/inbox/<包>` | 校验并导入任务包 |
| `dfctl status` | 队列、镜头状态、节点、告警 |
| `dfctl show <包> [镜头]` | 包或镜头详情（全部 take、审核报告、事件） |
| `dfctl tasks` / `dfctl decide <id> --action approve\|reject\|retry` | 人工队列 |
| `dfctl pause` / `dfctl resume` | 暂停或恢复领取生成任务 |
| `dfctl resume-node <节点>` | 解除过热/ECC 排空 |
| `dfctl profile <节点> <profile>` | 切换 GPU 布局 |
| `dfctl export <包>` | 导出签名结果包 |

Prometheus 抓取控制服务的 `/metrics` 和各节点 DCGM（:9400）。
