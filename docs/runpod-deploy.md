# RunPod 部署与验证

RunPod GitHub builder 从仓库根目录构建 `.runpod/Dockerfile`。基础镜像为
`codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0`。六个 native 模型在构建时下载至
`/opt/comfyui-baked/models`，以固定 revision、大小与 SHA256 校验。

## 无卷部署

- Endpoint：`3w2llhe9btnsmn`
- 模型来源：`H3_MODEL_SOURCE=image`
- 模板目录：`/opt/workflows/native`
- 卷挂载：`volumes=[]`、`networkVolumeIds=[]`
- Worker：`min=0`、`max=1`、`idleTimeout=5`、FlashBoot OFF
- GPU：`AMPERE_80`、`BLACKWELL_96`，CUDA 13.0

构建成功后先确认新内部镜像已部署，再解除卷挂载验证。原 Network Volume
`icf8p97vyb` 保留作回退，测试成功不等于已删除此卷，也不自动停止它的存储费。

## 冷启动测试

每项任务前先暂停 Endpoint，确认旧 worker 不再运行，然后恢复 max=1 并从 Generator 提交任务。
保存 Generator task id、RunPod job id、worker id、GPU、排队等待、执行时间、总耗时、费用和产物。

这测量的是容器/模型进程冷启动；宿主机镜像层或 OS 文件缓存可能已缓存，不等于强制重新拉取镜像。
高分辨率参数 `720p` 当前映射到 1344×768，报告需按产物实际尺寸记录。

## 验收

1. RunPod build state=COMPLETED，test=SUCCESS。
2. Endpoint global/network mounts 均为空。
3. health 的 model_source=image，模型六个文件大小匹配 lock，模板仅 native。
4. Generator 完成真实生成并返回带元数据的视频。
5. 每项记录冷启动依据，最后恢复 min=0 的自动缩容配置。

健康检查只说明 handler 的状态；ComfyUI 为 booting 时不能作为完成推理的证据。
