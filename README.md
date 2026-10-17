# MiniMax H3 Native — ComfyUI RunPod Serverless Worker

这是一个只提供 **native FL2VA / Ref2VA** 的 ComfyUI RunPod Serverless worker。
增强节点、增强工作流、QwenVL prompt enhancer、RIFE、TensorRT upscaler、face detailer
和 wildcard 已从 Dockerfile、镜像和生产 Network Volume 清单中移除。

## 当前部署

- Endpoint：`3w2llhe9btnsmn`
- GitHub：`beautyaiClub/runpod-comfyui-minimax-h3` / `main`
- 基础镜像：`codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0`
- CUDA：13.0
- ComfyUI：v0.40.0
- 模型卷：Network Volume `icf8p97vyb`，`US-MD-1`，160GB，挂载 `/runpod-volume`
- 当前支持模板：`MiniMaxH3-Native-FL2VA.json`、`MiniMaxH3-Native-Ref2VA.json`

Global Volume 已删除。生产只使用 Network Volume。

## 构建

根 `Dockerfile` 和 `/.runpod/Dockerfile` 都以现有 CUDA 13.0 运行时镜像为基础，
只复制 native handler、native URL loaders 和 native 模板，并删除基础镜像中继承的增强节点：

```dockerfile
FROM codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0
```

生产模型不放入镜像，模型从 Network Volume 读取。RunPod GitHub builder 只需要构建代码层，
不需要在构建阶段下载 77GB 模型，也不再尝试导出 90GB 镜像。

本地代码检查：

```powershell
wslc build --progress plain --tag runpod-comfyui-minimax-h3:native .
```

## Native 模型清单

`models-manifest.txt` 只包含 native 模板实际引用的 6 个文件：

```text
models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
models/text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
models/loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors
models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
```

以下内容已从生产 Network Volume 清理目标中排除：

- Singularity ref2va 模型
- QwenVL / GGUF 模型
- face detector / SAM
- character-swap / realism LoRA
- TAE preview VAE
- TensorRT RIFE / upscaler engine

## 请求格式

不再支持 `enhance=true`、`template_set=enhancers` 或增强工作流。提交这些参数会直接返回
明确错误，不会启动一个空闲 GPU worker。

### FL2VA / T2VA

```json
{
  "input": {
    "workflow": "MiniMaxH3-Native-FL2VA.json",
    "template_set": "native",
    "prompt": "[STATIC SHOT] a golden retriever puppy sleeping on a sunlit wooden floor",
    "config": "native_turbo",
    "seconds": 5,
    "resolution": "480p",
    "aspect_ratio": "16:9"
  }
}
```

### Ref2VA

```json
{
  "input": {
    "workflow": "MiniMaxH3-Native-Ref2VA.json",
    "template_set": "native",
    "prompt": "a person walking through a neon street",
    "config": "native_turbo",
    "seconds": 5,
    "images": ["https://example.com/reference.png"],
    "videos": ["https://example.com/reference.mp4"],
    "audios": ["https://example.com/reference.mp3"]
  }
}
```

## Network Volume 目录

```text
/runpod-volume/models/...
/runpod-volume/workflows/native/MiniMaxH3-Native-FL2VA.json
/runpod-volume/workflows/native/MiniMaxH3-Native-Ref2VA.json
```

启动时只 seed `native` 模板，不会创建或复制 `enhancers` 目录。
`VOLUME_AUTOPOPULATE=false` 时 worker 不下载模型；模型由预灌卷提供。

## 环境变量

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `H3_TEMPLATE_SET` | `native` | 固定使用 native 模板集 |
| `WORKFLOW_SEED` | `true` | 首次启动把 native 模板写入卷 |
| `VOLUME_AUTOPOPULATE` | `false` | 是否启动时下载 native 模型 |
| `JOB_TIMEOUT_S` | `1800` | 单任务超时 |
| `COMFYUI_EXTRA_ARGS` | 空 | ComfyUI 额外启动参数 |

## 已验证

Network Volume 回切后 native Generator 480p 已完成，输出为约 5 秒 MP4。
增强节点测试已取消，因为增强节点已从项目中移除。

长时间无人使用时保持 `workers.max=0`；提交任务前设置 `workers.max=1`，避免空闲计费。
