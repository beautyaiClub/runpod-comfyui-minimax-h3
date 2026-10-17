# MiniMax H3 Native — ComfyUI RunPod Serverless

基于 ComfyUI 的 MiniMax H3 视频与音频生成服务，支持文本生成、首帧生成、首尾帧生成，
以及参考图、参考视频和参考音频生成。通过 RunPod Serverless handler 接收任务并返回 MP4。

## 运行环境

- 基础镜像：`codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0`
- CUDA 13.0、ComfyUI v0.40.0
- 单张 A100 80GB 或 RTX PRO 6000 Blackwell 96GB
- 模型与工作流随镜像提供，部署无需挂载模型卷

## 构建与部署

RunPod GitHub 集成使用 `main` 分支和 `/.runpod/Dockerfile`，构建上下文为仓库根目录。
构建期间下载 6 个模型，按 `models-lock.json` 中的固定版本、文件大小及 SHA256 校验，
模型总大小为 46,378,958,507 字节，约 46.38GB。

```dockerfile
FROM codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0
```

根 `Dockerfile` 与 `.runpod/Dockerfile` 内容相同，可在本地复现构建：

```powershell
wslc build --progress plain --tag runpod-comfyui-minimax-h3:native .
```

Worker 直接读取 `/opt/comfyui-baked/models` 中的模型和 `/opt/workflows/native` 中的模板。
容器盘用于输入、输出和运行缓存。Serverless 可设置 `workers.min=0`、`workers.max=1`、
`idleTimeout=5`，任务完成后按空闲超时缩容。

## 输入方式

| 输入 | 参数 | 模板 |
| --- | --- | --- |
| 文本生成 | `prompt` | `MiniMaxH3-Native-FL2VA.json` |
| 首帧生成 | `image1` | `MiniMaxH3-Native-FL2VA.json` |
| 首尾帧生成 | `image1`、`image2` | `MiniMaxH3-Native-FL2VA.json` |
| 参考素材生成 | `images`、`videos`、`audios` | `MiniMaxH3-Native-Ref2VA.json` |

`workflow="auto"` 按输入自动选择模板。首尾帧输入与参考素材输入互斥。
参考通道最多支持 9 张图、3 段视频、3 段音频，素材使用可直接访问的 HTTP(S) URL。

```json
{
  "input": {
    "workflow": "auto",
    "prompt": "A golden retriever rests on a sunlit wooden floor, static camera",
    "seconds": 5,
    "resolution": "480p",
    "aspect_ratio": "16:9"
  }
}
```

首尾帧示例：

```json
{
  "input": {
    "workflow": "auto",
    "prompt": "A slow, continuous camera move between the two views",
    "image1": "https://example.com/first.png",
    "image2": "https://example.com/last.png",
    "seconds": 5,
    "resolution": "768p",
    "aspect_ratio": "16:9"
  }
}
```

参考素材示例：

```json
{
  "input": {
    "workflow": "auto",
    "prompt": "Keep the subject and motion consistent with the references",
    "images": ["https://example.com/reference.png"],
    "videos": ["https://example.com/reference.mp4"],
    "seconds": 5,
    "resolution": "480p",
    "aspect_ratio": "16:9"
  }
}
```

## 参数

| 参数 | 说明 |
| --- | --- |
| `prompt` | 画面、动作、运镜和声音描述 |
| `workflow` | `auto` 或具体模板名 |
| `template_set` | `native` |
| `seconds` | 输出目标时长；帧数按 H3 支持的网格对齐 |
| `resolution` | `352p`、`480p`、`576p`、`768p`；`720p` 为 `768p` 档别名 |
| `aspect_ratio` | `16:9`、`9:16`、`1:1`、`4:3`、`3:4`、`21:9` |
| `config` | `native_turbo`（默认）或 `native` |
| `params` | 按节点 ID 与 widget 名覆盖参数 |

尺寸按 32 像素对齐；16:9 的 480p 档输出为 832×480，高分辨率档输出为 1344×768。
返回的时长和尺寸以视频实际元数据为准。

## 模型

| 文件 | 用途 |
| --- | --- |
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | H3 INT8 主模型 |
| `qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors` | 文本编码器 |
| `minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` | FL2VA Turbo LoRA |
| `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | Ref2VA Turbo LoRA |
| `minimax_h3_video_vae_fp16.safetensors` | 视频 VAE |
| `minimax_h3_audio_vae_fp32.safetensors` | 音频 VAE |

## 输出与健康检查

返回 `outputs`、`images`、`texts` 和 `widget_repairs`。小产物以 base64 回传；
大产物可通过请求中的 `s3Config` 写入对象存储并返回 URL。

`{"input":{"action":"health"}}` 返回 ComfyUI 启动状态、模板清单、模型目录、
模型文件大小与 `model_source=image`。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `COMFYUI_DIR` | `/opt/comfyui-baked` | ComfyUI 目录 |
| `WORKFLOWS_DIR` | `/opt/workflows` | 模板根目录 |
| `H3_TEMPLATE_SET` | `native` | 模板集 |
| `H3_MODEL_SOURCE` | `image` | 模型来源 |
| `JOB_TIMEOUT_S` | `1800` | 单任务等待超时 |
| `COMFYUI_EXTRA_ARGS` | 空 | ComfyUI 额外启动参数 |
