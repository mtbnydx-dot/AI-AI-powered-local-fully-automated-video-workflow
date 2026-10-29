# 离线 AI 视频生产

从脚本包出发，经 ComfyUI 生成镜头、vLLM/Qwen 评审、局部返工、剪辑、人工审核与超分，输出视频和完整运行记录。只实现视频生产模块；原应用代码保留在 Git 历史中。

## CPU 上运行

需要 Python 3.10+。FFmpeg 由 `imageio-ffmpeg` 提供，无需 GPU、模型或系统 FFmpeg。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/videopipe validate examples/product/package.json --config configs/cpu.yaml
.venv/bin/videopipe run examples/product/package.json --config configs/cpu.yaml --output runs/cup
.venv/bin/videopipe status runs/cup
```

样例包含三镜头脚本、正负 Prompt、验收条件和原创参考图。CPU 配置实际编码、拼接和放大视频，并刻意安排一次镜头拒绝与一次整片拒绝，验证局部返工。输出目录需为新目录。

**CPU 后端是参考图运动预览和技术检查，不运行 AI，也不判断语义画质。** `lanczos` 是普通插值放大；GPU 配置的 `comfyui` 使用真实超分模型。

## 运行与返工

```bash
videopipe resume runs/cup
videopipe retry runs/cup --shot shot-02 --feedback '保持杯把形状' --auto-approve
videopipe retry runs/cup --shot shot-02 --prompt 'A stationary orange ceramic mug, slow camera push-in.'
videopipe approve runs/cup
```

`resume` 复用已通过镜头；Review 服务失败时复用已生成片段。`retry` 重做指定镜头并重新评审整片，保留旧尝试和交付版本。生产配置在 `awaiting_approval` 停下，`approve` 后超分交付；无人值守测试可显式使用 `--auto-approve`。CPU 配置默认自动通过人工环节。

预算耗尽、服务失败或无法自动执行的剪辑建议会保留结果和原因。已完成、待审核返回退出码 0，需要处理返回 2，输入或运行错误返回 1。不要同时用多个 CLI 进程操作同一运行目录。

## GPU 测试与离线部署

| 配置 | 用途 |
| --- | --- |
| `configs/cpu.yaml` | 无模型全流程与返工验证 |
| `configs/pro6000.yaml` | 单卡 ComfyUI/H3，CPU 代替 Review，不要求同时装下两种模型 |
| `configs/b200.yaml` | 7 个单 GPU ComfyUI 实例与 1 个 vLLM/Qwen 实例，GPU 划分可改 |

本项目不捆绑 ComfyUI、vLLM 或模型。使用独立推理环境、数据盘模型和本地服务地址。准备与启动见[离线部署](docs/deployment.md)，API workflow 与模型见[ComfyUI 接入](docs/comfyui.md)，评审协议与限制见[Review](docs/review.md)。GPU 配置是待实机验证的起点，不代表已证明显存、吞吐和画质。

```bash
videopipe run examples/product/package.json --config configs/pro6000.yaml --output /mnt/data/runs/cup-gpu
python scripts/export_bundle.py --output bundles/videopipe-cpu.tar.gz
```

## 验证与代码入口

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/build_workflows.py
.venv/bin/python scripts/create_example.py
```

测试包含真实 FFmpeg 媒体处理、CPU 全流程和本地 HTTP 服务的 ComfyUI/vLLM 契约测试；HTTP 测试服务不执行模型。后两条命令从源头重建内置 workflow 与样例，避免手改生成文件。

```text
videopipe/
  cli.py              # CLI 入口
  models.py           # 脚本包、配置、评审类型
  inputs.py           # 输入校验与路径解析
  pipeline.py         # 生成、评审、返工、审核与交付
  records.py          # JSON 状态与 JSONL 事件
  media.py            # FFmpeg 媒体处理
  backends/           # ComfyUI、vLLM、CPU 替代推理
  workflows/          # 随包分发的原生 API 图
configs/              # CPU、单卡、8 卡运行配置
deploy/               # 外部推理服务启动脚本
examples/product/     # 自包含样例脚本包
tests/                # 实现与流程测试
```

[设计记录](docs/README.md) · [输入与运行记录](docs/usage.md) · [初版验收](docs/acceptance.md)
