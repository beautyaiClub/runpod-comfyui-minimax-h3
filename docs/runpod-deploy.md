# RunPod 部署备忘

## 当前生产配置

| 项目 | 值 |
| --- | --- |
| Endpoint | `3w2llhe9btnsmn` / `myreels-minimax-h3` |
| GitHub | `beautyaiClub/runpod-comfyui-minimax-h3` / `main` |
| 基础镜像 | `codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0` |
| 生产卷 | Network Volume `icf8p97vyb` / `myreels-h3-models` |
| 机房 | `US-MD-1` |
| 挂载路径 | `/runpod-volume` |
| 容器盘 | 30GB |
| GPU 池 | `AMPERE_80`、`BLACKWELL_96` |
| Worker | `min=0`、`max=1`、`idleTimeout=5` |

Global Volume 已删除。Endpoint 当前没有 global mount，只有 Network Volume：

```json
{
  "global": [],
  "network": [{"volumeId": "icf8p97vyb"}]
}
```

## Native-only 变更

本次 native-only 版本已经从代码和构建中删除：

- ComfyUI-RIFE-TensorRT-Auto
- ComfyUI-Upscaler-TensorRT-Auto
- ComfyUI-Pixaroma
- ComfyUI-Impact-Pack / Impact-Subpack
- ComfyUI-Easy-Use
- ComfyUI-QwenVL-Mod
- ComfyUI-PerfectVideoResolution
- ComfyUI-TagForge
- 所有 `workflows-api/enhancers/*.json`
- QwenVL GGUF、Singularity、face/SAM、style/character LoRA、TAE 和 TensorRT 文件

`enhance=true` 会由 handler 直接拒绝，避免启动一个不会真正进入 GPU 推理的增强任务。
生产只保留：

```text
MiniMaxH3-Native-FL2VA.json
MiniMaxH3-Native-Ref2VA.json
```

## Dockerfile 构建

根 `Dockerfile` 和 `/.runpod/Dockerfile` 都使用：

```dockerfile
FROM codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0
```

这个基础镜像已经包含 CUDA 13.0、ComfyUI 和 Python runtime。新的 Dockerfile 只做以下事情：

1. 删除基础镜像里继承的增强 custom nodes；
2. 复制 native URL loaders、handler 和 native workflows；
3. 将 `models-manifest.txt` 限制为 native 所需模型；
4. 设置 `VOLUME_AUTOPOPULATE=false`，从 Network Volume 读取模型。

这样 RunPod builder 不会再次解析和安装整套 Python/CUDA 依赖，也不会下载和导出 90GB 模型镜像。

## Network Volume 内容

```text
/runpod-volume/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
/runpod-volume/models/text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
/runpod-volume/models/loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors
/runpod-volume/models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
/runpod-volume/models/vae/minimax_h3_video_vae_fp16.safetensors
/runpod-volume/models/vae/minimax_h3_audio_vae_fp32.safetensors
/runpod-volume/workflows/native/MiniMaxH3-Native-FL2VA.json
/runpod-volume/workflows/native/MiniMaxH3-Native-Ref2VA.json
```

生产卷中应删除增强专用文件和目录：

```text
models/diffusion_models/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors
models/LLM/
models/ultralytics/
models/sams/
models/vae_approx/
models/tensorrt/
models/loras/h3_character_swap_pro4500_1000.safetensors
models/loras/h3-realism-people-t2v-i2v-r2v.safetensors
workflows/enhancers/
```

删除前先核对 native 六个模型文件存在且大小正确；不要删除 native 所需的 FL2VA、text encoder、
两个 native LoRA 和两个 VAE。

## 冷启动和费用

RunPod worker 从启动到停止按秒计费。长期无人使用时：

```powershell
pwsh scripts/runpod-endpoint.ps1 pause
```

生成前恢复 `workers.max=1`。Network Volume 约 160GB，费用约 `$11.20/月`；Global Volume
已经删除，不再产生其存储费用。

## 验证

先做 health：

```bash
curl -s -X POST "https://api.runpod.ai/v2/3w2llhe9btnsmn/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"input":{"action":"health"}}'
```

期望只出现 `workflow_sets.native`，并返回：

```text
volume_mounted=true
workflows_dir=/runpod-volume/workflows
```

再通过 Generator 端测试 native 480p 和 768p。不要使用 `enhance=true`；该参数已被 native-only
handler 明确拒绝。

## 排障

| 现象 | 处理 |
| --- | --- |
| 任务一直 IN_QUEUE | Network Volume 绑定 `US-MD-1`，检查该机房 GPU 容量 |
| 找不到模型 | 检查 Network Volume 上 native 六个模型文件和 `extra_model_paths.yaml` |
| 找不到模板 | 检查 `/runpod-volume/workflows/native` 两个 JSON |
| `enhance=true` 被拒绝 | 预期行为；当前版本已移除增强节点 |
| GPU 0% 且任务卡在 progress=40 | 取消任务，检查是否误用了旧增强模板或旧 worker 镜像 |

## GitHub Endpoint 与 RunPod Hub

生产部署不需要上架 Hub，也不需要 GitHub Release。私有 Endpoint 直接关联仓库的 `main` 分支，
RunPod 根据 `/.runpod/Dockerfile` 构建内部镜像即可。

`.runpod/hub.json` 和 `.runpod/tests.json` 只在以后选择上架 Hub 时使用；它们不是私有 GitHub
Endpoint 的必要条件。即使上架 Hub，Hub 也应使用这份完整 Dockerfile，不需要 Docker Hub 镜像。

`.runpod/tests.json` 当前只跑 `{"action":"health"}`，健康检查不依赖模型卷、不下载模型。真实
FL2VA / Ref2VA 仍应在新内部镜像构建成功后通过 Endpoint 单独验证。
