<!--
<document version="0.2.0" updated_at="2026-10-28T21:51:26+08:00" keywords="设计记录,范围,AI视频生产" />
-->

# AI 视频生产框架：设计记录

本文档组记录已对齐的设计，供人工审核和后续 agent 开发参考。

- 从头实现新架构。原始代码仅作为功能、参数和 ComfyUI workflow 的参考，已在 PRO6000 跑通的链路作为验证对照。
- 系统分为脚本包生成和视频生成，当前优先建设视频生产模块。
- 生产操作使用 CLI，目标环境为离线的 8 卡 B200 节点。

| 文档 | 内容 |
| --- | --- |
| [模块架构](architecture.md) | 模块职责、ComfyUI 与 vLLM 的集成边界 |
| [生产工作流](workflow.md) | 脚本包、视频生成、Review、人工审核与交付 |
| [开发与部署约定](engineering.md) | 外部依赖、离线部署、测试环境、运行记录与开发约束 |
| [初版验收](acceptance.md) | 实施范围与验收证据 |
| [输入与运行记录](usage.md) | 脚本包、CLI、返工、运行目录 |
| [ComfyUI 接入](comfyui.md) | H3、超分 workflow、模型与协议 |
| [Review 接入](review.md) | vLLM/Qwen 推理、结构化评审与能力边界 |
| [离线部署](deployment.md) | CPU 应用包、GPU 运行环境与服务启动 |
