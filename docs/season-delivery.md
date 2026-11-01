# 整季运行、交付与字幕

一次运行整季，按 `overview.json` 的 `episodes` 顺序逐集调用现有单集流程；每集使用自身的 `production.yaml`，集内仍按原配置并发。重复执行同一命令会跳过已完成的集，并恢复未完成的集。某集报错会记录后继续下一集，最终以非零退出码报告未完成。

```bash
videopipe run-season examples/pearl_shirt_long \
  --output /mnt/video-runs/pearl_shirt_long
```

可用 `--config CONFIG` 统一覆盖各集配置。各集默认不要求人工批准；显式启用了人工批准的配置仍遵循单集规则，也可传 `--auto-approve`。

只重新收集已有成片，不启动模型或重生成视频：

```bash
videopipe collect /mnt/video-runs/pearl_shirt_long
# 旧 Tide 的 smoke/full 也使用相同收集器，可指定独立的交付目录：
videopipe collect /mnt/video-runs/tide-b200-20261030 \
  --output /mnt/video-delivery/tide
```

运行目录：

```text
output/
├── runs/ep01/ … ep08/       # 单集工作目录与恢复状态
├── state.json              # 各集交付状态及错误
├── events.jsonl            # 整季运行顺序与错误
├── delivery/
│   ├── ep01/ … ep08/
│   │   ├── final.mp4
│   │   ├── episode.srt
│   │   └── production_report.json
│   ├── logs/              # 每集事件、运行和服务日志
│   └── summary.json / summary.md
└── delivery.tar.gz         # 只包含本次收集的交付文件
```

Tide 原有 `python scripts/run_b200_tests.py --phase all --output ...` 保留，交付改为 `delivery/smoke/` 与 `delivery/full/`。不再生成嵌套的 `logs.tar.gz`，归档不含工作目录、输入图片、审核抽帧或完整请求数据。重新收集不会清理旧目录；旧归档等遗留文件不会进入新的压缩包。

字幕正文保留剧本 `<d>` 中的对白。使用镜头实测时长建立全片时间轴，能匹配 ASR 时采用观察到的时间；其余对白按所在镜头或相邻已对齐对白之间的时间分配，报告标记为 `script`，混合结果标记为 `mixed`。无对白也输出空 `episode.srt`。粗对齐字幕不证明音频实际说全了台词。

ASR 接收端保留有效句子，单个词的时间戳无效时使用句级时间，不丢弃整集转写。台词校验只发送预期对白列表和 ASR 句级文本，容忍断句、标点及含义不变的表达差异，只记录实质差异或不确定性，不决定字幕是否输出。

收集旧运行时会使用已保存的 `worker-result.json` 恢复可用 ASR，并补全 SRT、更新字幕报告；不会调用 ASR 或 Qwen 服务重新推理。现有台词校验结论保持原记录。
