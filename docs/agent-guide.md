# 开发与运行入口

先读 [工作流](workflow.md) 和 [架构](architecture.md)，按问题所在阶段定位代码。运行证据集中在该次任务的配置、状态、事件与请求/响应文件中。

## 保持的机制

| 改动位置 | 需要保持 |
| --- | --- |
| 剧本与导出 | 角色、服装、地点、道具、镜头 ID 及引用一致；作品细节来自原文和 notes。修改导出格式时同步检查图片清单与视频输入 |
| 图片 | 模板位于 `prompt/image/`；引用顺序决定上传序号和依赖。续跑沿用 `tasks.json`、已有 job ID 和资产路径 |
| 视频 | 有效 prompt 与素材在创建运行时冻结；返工只变 seed、沿用预算；新增制作轮由 `--new-round` 表达 |
| 选片与声音 | 保留审核不可用、预算耗尽等原因；字幕使用原台词，ASR / 差异报告不控制视频返工 |
| Workflow 与部署 | 编辑 `scripts/build_workflows.py` 后生成内置图；ComfyUI、vLLM 和模型在外部环境管理，保留来源和许可 |
| 状态 | 一个控制进程写一个运行目录；改变输入或模板后用新运行目录，历史快照留作复盘 |

## 验证与排查

```bash
.venv/bin/python -m unittest discover -s tests -v
```

| 改动范围 | 现有验证入口 |
| --- | --- |
| 图片接口、依赖和交付 | `test_imagepipe_client.py`、`test_imagepipe_pipeline.py`、`test_imagepipe_delivery.py` |
| 输入、预算、调度、服务恢复 | `test_pipeline.py`、`test_pipeline_http.py`、`test_comfyui.py` |
| 画面审核与音频 | `test_review.py`、`test_asr.py`、`test_subtitles.py`、`test_media.py` |
| 整季和交付收集 | `test_season.py`、`test_delivery.py` |
| 模板或资源移动 | 核对调用路径和 `pyproject.toml`，检查构建后的 wheel 中能加载模板 |

本地回归覆盖 CPU 媒体和 HTTP 契约；GPU 模型兼容、峰值显存、吞吐与画质通过目标节点运行记录确认。排查位置和常用命令见 [使用](usage.md)。
