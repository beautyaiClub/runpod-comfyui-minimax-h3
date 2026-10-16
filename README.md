# MiniMax H3 — ComfyUI RunPod Serverless Worker

把 ComfyUI + MiniMax H3（FL2VA / Ref2VA，带音频）打包成 RunPod Serverless 端点：
每个 worker 启动一次 ComfyUI，handler 按 job 取模板、填参数、注入素材、投递到本地
ComfyUI，再把产物回传。模型在 RunPod 构建阶段直接打包进镜像，生产端点不需要
Global Volume、Network Volume，也不需要 worker 首次启动时下载模型。

镜像基座：`ubuntu:24.04` + CUDA 13.0 + `torch==2.10.0+cu130`，ComfyUI 固定
`v0.40.0`（都在 Dockerfile 顶部的 ARG 里，改一处即可）。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `Dockerfile` | 两阶段构建：builder 装 ComfyUI + 节点 + Python 依赖，runtime 只带运行时 |
| `handler.py` | RunPod handler：模板选择、参数注入、素材注入、widget 校正、产物回传 |
| `serverless-entrypoint.sh` | 开机流程：选择镜像内模型、兼容旧卷挂载、启动 handler |
| `populate-volume.sh` + `models-manifest.txt` | 模型清单；Dockerfile 构建时按清单下载，脚本仅保留给旧卷部署 |
| `workflows-api/native/` | 原生模板集（核心节点 + 三个 URL loader） |
| `workflows-api/enhancers/` | 增强模板集（提示词增强、RIFE 插帧、TensorRT 超分、face detailer、wildcard） |
| `custom_nodes/h3_url_loaders/` | 自带的两个 URL loader（从上游 vendor，见该目录 README） |
| `scripts/` | 模板生成、模板校验、handler 自检、Manager 缓存预热 |
| `.runpod/hub.json` | RunPod Hub 清单（端点默认环境变量在这里声明） |

## 构建

```bash
# RunPod GitHub builder 使用的同一套 Dockerfile；模型会在构建阶段下载并进入镜像。
# 本地执行会下载约 77.6GB 模型，仅用于需要本地复现时。
wslc build --progress plain --build-arg BAKE_MODELS=true .

# 代码自检镜像（不下载模型，不用于生产部署）
wslc build --progress plain --build-arg BAKE_MODELS=false \
  --tag runpod-comfyui-minimax-h3:code-only .
```

常用构建参数（都在 Dockerfile 顶部）：

| ARG | 默认 | 说明 |
| --- | --- | --- |
| `COMFYUI_VERSION` | `v0.40.0` | ComfyUI tag |
| `WITH_H3_ENHANCERS` | `true` | 是否装增强层（TensorRT / Impact / Easy-Use / Pixaroma / QwenVL / TagForge / PerfectVideoResolution） |
| `BAKE_MODELS` | `true` | 是否在 RunPod 构建阶段把 `models-manifest.txt` 中的模型下载到镜像 |
| `TORCH_VERSION` / `TORCHVISION_VERSION` / `TORCHAUDIO_VERSION` | `2.10.0+cu130` / `0.25.0+cu130` / `2.10.0+cu130` | CUDA 13 的 PyTorch |
| `CUDA_VERSION_DASH` / `TORCH_INDEX_SUFFIX` | `13-0` / `cu130` | CUDA 工具链与 PyTorch 源 |
| `TRT_CU13_VERSION` | `10.15.1.29` | TensorRT 版本，需与卷里预编译引擎一致 |

依赖锁定：builder 里用 `pip-compile` 生成精确版本锁（不带哈希——带上会把所有平台的
wheel 全下下来算哈希，几十 GB）；装完校验 torch 版本，再装增强层。

## 部署

1. RunPod Endpoint 关联本仓库的 GitHub 构建，Dockerfile 使用 `/.runpod/Dockerfile`。
2. RunPod builder 在构建阶段下载 `models-manifest.txt` 中的模型并保存构建产物，生产 worker 不再下载模型。
3. 不挂 Global Volume 或 Network Volume；容器盘保留 30GB 作为运行期临时空间即可，模型位于镜像层。
4. GPU：允许 `AMPERE_80` 和 `BLACKWELL_96`，单卡；两档均需通过实际任务验证。
5. `workers.max=0` 时没有任务 worker，不产生 GPU worker 运行费用；镜像构建和缓存由 RunPod 管理。

## 两套模板

两套模板都直接放在镜像内。生产 worker 默认读取 `/opt/workflows`，不需要卷。
修改模板后提交 GitHub 并触发 RunPod 重建即可。

```
/opt/workflows/native/MiniMaxH3-Native-FL2VA.json     t2va / i2va / fl2va
/opt/workflows/native/MiniMaxH3-Native-Ref2VA.json    ref2va
/opt/workflows/enhancers/*.json                       5 个增强模板
```

选哪套由参数决定：

| 参数 | 位置 | 默认 |
| --- | --- | --- |
| `template_set` | job input | 取端点环境变量 |
| `H3_TEMPLATE_SET` | 端点环境变量 | `native` |

原生模板的槽位按 job 顺序填，填不满的槽位连同连线一起剪掉，所以同一个模板覆盖
t2va（不给图）/ i2va（1 张图）/ fl2va（首尾两帧），ref2va 模板最多 9 图 + 3 视频 +
3 音频，给多少填多少。

## Job 输入

```json
{
  "input": {
    "workflow": "MiniMaxH3-Native-Ref2VA.json",
    "template_set": "native",
    "prompt": "a woman walks through a neon alley, [DOLLY IN]",
    "config": "native_turbo",
    "seconds": 6,
    "images": ["https://…/ref1.png", "https://…/ref2.png"],
    "videos": ["https://…/ref.mp4"],
    "audios": ["https://…/ref.mp3"],
    "upscale": true,
    "rife": true
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `workflow` | 模板文件名（在 `template_set` 对应的目录里） |
| `template_set` | `native` / `enhancers` |
| `prompt` | 提示词；模板里所有名为 `prompt` 的输入都会被替换 |
| `config` | 预设：`native` / `native_turbo` / `r2va_singularity` / `r2va_singularity_turbo` / `10eros` / `10eros_turbo`，负责换 unet、挂 LoRA、改 steps/sampler/shift |
| `camera_tag` | 写进模板里的 `camera_tag` 输入 |
| `seconds` / `image_size` / `resolution` | 覆盖时长（×24 帧）与分辨率 |
| `images` / `videos` / `audios` | 原生模板按顺序填 URL loader；增强模板沿用上传逻辑（`images` 上传、`video` 单条） |
| `params` | 通用覆盖：`{"节点id:widget": 值}` |
| `upscale` / `rife` / `enhance` / `lora` / `vl_preset` | 增强集的开关 |
| `prompt_graph` | 直接传 API 格式图（跳过模板） |
| `s3Config` | 大产物走 S3（RunPod 卷的 S3 API）并返回预签名 URL；小文件直接 base64 回传 |
| `action: "health"` | 返回 `comfyui` 状态、`workflow_set`、`workflow_sets`（两套各自文件清单）、`workflows_dir`、`volume_mounted`，不碰模型 |

响应：`{"outputs": [...], "texts": {...}, "widget_repairs": [...]}`——`widget_repairs`
列出本次投递前自动修正过的 widget（见下）。

## 环境变量

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `H3_TEMPLATE_SET` | `native` | 默认模板集 |
| `BAKED_MODELS` | `true` | 是否优先使用镜像内模型 |
| `VOLUME_AUTOPOPULATE` | `false` | 仅旧卷兼容模式下是否下载缺失模型 |
| `HF_TOKEN` | 空 | 从 Hugging Face 拉 gated 仓库时需要 |
| `JOB_TIMEOUT_S` | `1800` | 单任务超时（秒） |
| `COMFYUI_EXTRA_ARGS` | 空 | 传给 ComfyUI 的额外启动参数 |
| `OUTPUT_INLINE_MAX_MB` / `OUTPUT_INLINE_BUDGET_MB` | `10` / `15` | 产物回传的 base64 上限 |
| `S3_OUTPUT_PREFIX` | `outputs` | 卷上产物目录前缀 |
| `S3_URL_TTL_S` | `3600` | 预签名 URL 有效期 |
| `WORKFLOWS_DIR` | `/opt/workflows` | 模板根目录（有网络卷时 entrypoint 会指向卷） |
| `VOL_ROOT` / `MANIFEST` | `/runpod-volume` / 脚本同目录清单 | `populate-volume.sh` 的卷与清单位置 |

## 模型

模型清单是 `models-manifest.txt`。RunPod 构建 `/.runpod/Dockerfile` 时，Dockerfile
逐条下载清单中的 14 个文件到 `/opt/comfyui-baked/models`，下载完成后再复制到最终镜像。
当前模型集合约 77.6GB，连同 ComfyUI、CUDA 和节点后最终镜像预计约 90～95GB。

清单包含：Comfy-Org 的 H3 INT8 权重与 VAE、Singularity ref2va int8、Qwen3-VL 32B
NVFP4 文本编码器、lightx2v turbo LoRA、风格 LoRA、face detailer 用的 bbox / sam 权重。
生产环境不需要单独创建模型卷。`populate-volume.sh` 仅保留给旧的卷挂载部署，不参与默认镜像构建后的启动流程。

## 节点来源

固定到 commit，来源与用途：

| 节点包 | 来源仓库 | 用途 |
| --- | --- | --- |
| `ComfyUI-Manager` · `ComfyUI-KJNodes` · `Civicomfy` · `ComfyUI-RunpodDirect` | `ltdrdata/ComfyUI-Manager`、`kijai/ComfyUI-KJNodes`、`MoonGoblinDev/Civicomfy`、`MadiatorLabs/ComfyUI-RunpodDirect` | 基础包（与 RunPod 官方 comfyui-base 同一批 pin） |
| `ComfyUI-VideoHelperSuite` | `Kosinkadink/ComfyUI-VideoHelperSuite` | 视频合流 |
| `comfyui-beautyai` | `beautyaiClub/comfyui-beautyai` | `BeautyAI_LoadAudioFromURL`（参考音频） |
| `h3_url_loaders`（本仓库） | `LoadImageFromUrl` 取自 `sipherxyz/comfyui-art-venture`，`LoadVideoFromURL` 取自 `MixLabPro/comfyui-mixlab-nodes` | 图 / 视频 URL 载入，只 vendor 了用到的节点 |
| `ComfyUI-RIFE-TensorRT-Auto` | `silveroxides/ComfyUI_RIFE_TensorRT_Auto` | 插帧（增强集） |
| `ComfyUI-Upscaler-TensorRT-Auto` | `yuvraj108c/ComfyUI-Upscaler-Tensorrt` | TensorRT 超分（增强集） |
| `ComfyUI-Pixaroma` · `ComfyUI-Impact-Pack` · `ComfyUI-Impact-Subpack` · `ComfyUI-Easy-Use` | `pixaroma`、`ltdrdata`、`yolain` | UI 辅助与 face detailer（增强集） |
| `ComfyUI-QwenVL-Mod` · `ComfyUI-TagForge` · `ComfyUI-PerfectVideoResolution` | `huchukato/ComfyUI-QwenVL-Mod`、`huchukato/ComfyUI-TagForge`、`huchukato/ComfyUI-PerfectVideoResolution` | 提示词增强与 wildcard（增强集）；`QwenVL_Unified`、`WildcardProcessor`、`ComfyUI-PerfectVideoResolution` 这三个节点名只有这三个仓库提供，没有别的上游 |

## 自检

不需要 GPU、不需要模型卷：

```bash
# IMG 指向 RunPod 构建完成后的内部镜像；本地代码自检也可使用本地 tag。
IMG=<runpod-built-image>

# 1) 原生图要用的 19 个节点是否都在 + 三个 loader 的 schema 是否与参考实例一致
wslc run --rm --entrypoint /usr/bin/python3 $IMG /opt/serverless/verify-native-h3.py --boot

# 2) 两套模板用到的节点/入参在这个镜像上是否都能跑
wslc run --rm --entrypoint /bin/bash $IMG -lc \
  "python3 /opt/comfyui-baked/main.py --cpu --listen 127.0.0.1 --port 8188 & sleep 45; \
   python3 /opt/serverless/validate-templates.py"

# 3) handler 的图改写逻辑（素材槽位、autogrow 引用、模型名兜底、widget 校正）
wslc run --rm --entrypoint /usr/bin/python3 $IMG /opt/serverless/test-handler-templates.py
```

期望结果：19/19 节点齐全、三个 loader schema 逐项一致、两套模板全部 `ok`、自检
`all template checks passed`。

## 已知限制

- 增强集的 RIFE / 超分节点首次运行会按当前 GPU 架构编译 TensorRT 引擎，可能需要几分钟；
  引擎不打包进镜像，也不跨 GPU 架构复用。
- 模板导出时的 widget 会随节点版本漂移（例如 `crf` 被挪进 format 控件、`2K` vs `2k`）。
  handler 在投递前按运行中服务器的 schema 校正：只差大小写的值做归一，节点不认识的
  widget 丢弃走默认值，改动明细放在响应的 `widget_repairs` 里。
- 只有 `linux/amd64`；未在非 Blackwell 卡上验证。
