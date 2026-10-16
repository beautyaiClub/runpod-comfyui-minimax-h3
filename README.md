# MiniMax H3 — ComfyUI RunPod Serverless Worker

把 ComfyUI + MiniMax H3（FL2VA / Ref2VA，带音频）打包成 RunPod Serverless 端点：
每个 worker 启动一次 ComfyUI，handler 按 job 取模板、填参数、注入素材、投递到本地
ComfyUI，再把产物回传。模型放在网络卷上，镜像里只有运行时和节点。

镜像基座：`ubuntu:24.04` + CUDA 13.0 + `torch==2.10.0+cu130`，ComfyUI 固定
`v0.40.0`（都在 Dockerfile 顶部的 ARG 里，改一处即可）。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `Dockerfile` | 两阶段构建：builder 装 ComfyUI + 节点 + Python 依赖，runtime 只带运行时 |
| `handler.py` | RunPod handler：模板选择、参数注入、素材注入、widget 校正、产物回传 |
| `serverless-entrypoint.sh` | 开机流程：材料化 ComfyUI、写 `extra_model_paths.yaml`、seed 模板、下载模型 |
| `populate-volume.sh` + `models-manifest.txt` | 网络卷模型清单与幂等下载（并发 worker 用 flock 串行） |
| `workflows-api/native/` | 原生模板集（核心节点 + 三个 URL loader） |
| `workflows-api/enhancers/` | 增强模板集（提示词增强、RIFE 插帧、TensorRT 超分、face detailer、wildcard） |
| `custom_nodes/h3_url_loaders/` | 自带的两个 URL loader（从上游 vendor，见该目录 README） |
| `scripts/` | 模板生成、模板校验、handler 自检、Manager 缓存预热 |
| `.runpod/hub.json` | RunPod Hub 清单（端点默认环境变量在这里声明） |

## 构建

```bash
# 默认：两套模板都能跑（含增强层，本地 13.8GB）
wslc build --progress plain --tag codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0 .

# 只要原生模板集：不装增强层，镜像 8.83GB
wslc build --progress plain --build-arg WITH_H3_ENHANCERS=false \
  --tag codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0-lean .
```

常用构建参数（都在 Dockerfile 顶部）：

| ARG | 默认 | 说明 |
| --- | --- | --- |
| `COMFYUI_VERSION` | `v0.40.0` | ComfyUI tag |
| `WITH_H3_ENHANCERS` | `true` | 是否装增强层（TensorRT / Impact / Easy-Use / Pixaroma / QwenVL / TagForge / PerfectVideoResolution） |
| `TORCH_VERSION` / `TORCHVISION_VERSION` / `TORCHAUDIO_VERSION` | `2.10.0+cu130` / `0.25.0+cu130` / `2.10.0+cu130` | CUDA 13 的 PyTorch |
| `CUDA_VERSION_DASH` / `TORCH_INDEX_SUFFIX` | `13-0` / `cu130` | CUDA 工具链与 PyTorch 源 |
| `TRT_CU13_VERSION` | `10.15.1.29` | TensorRT 版本，需与卷里预编译引擎一致 |

依赖锁定：builder 里用 `pip-compile` 生成精确版本锁（不带哈希——带上会把所有平台的
wheel 全下下来算哈希，几十 GB）；装完校验 torch 版本，再装增强层。

## 部署

1. 网络卷 ≥160GB（模型约 100GB），挂到 `/runpod-volume`
2. 容器盘：`hub.json` 里声明 30GB（13.8GB 镜像 + 运行期临时文件够用）
3. GPU：`BLACKWELL_96`，单卡（32B 文本编码器 + 视频 DiT + TensorRT 引擎吃显存）
4. 首次开机自动下载缺失模型到卷上（`VOLUME_AUTOPOPULATE=false` 可关闭）；模板也会
   在首次开机时 seed 到卷上

## 两套模板

模板不烧死在镜像里：首次开机 entrypoint 会把镜像内的两套复制到卷上，之后**不再覆盖**，
所以改模板 / 加模板只要换文件 + 重启 worker。

```
/runpod-volume/workflows/native/MiniMaxH3-Native-FL2VA.json     t2va / i2va / fl2va
/runpod-volume/workflows/native/MiniMaxH3-Native-Ref2VA.json    ref2va
/runpod-volume/workflows/enhancers/*.json                       5 个增强模板
```

选哪套由参数决定：

| 参数 | 位置 | 默认 |
| --- | --- | --- |
| `template_set` | job input | 取端点环境变量 |
| `H3_TEMPLATE_SET` | 端点环境变量 | `native` |
| `WORKFLOW_SEED` | 端点环境变量 | `true`（`false` 表示卷上模板你自己管） |

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
| `WORKFLOW_SEED` | `true` | 首次开机是否把镜像内模板 seed 到卷上 |
| `VOLUME_AUTOPOPULATE` | `true` | 首次开机是否按 `models-manifest.txt` 下载模型 |
| `HF_TOKEN` | 空 | 从 Hugging Face 拉 gated 仓库时需要 |
| `JOB_TIMEOUT_S` | `1800` | 单任务超时（秒） |
| `COMFYUI_EXTRA_ARGS` | 空 | 传给 ComfyUI 的额外启动参数 |
| `OUTPUT_INLINE_MAX_MB` / `OUTPUT_INLINE_BUDGET_MB` | `10` / `15` | 产物回传的 base64 上限 |
| `S3_OUTPUT_PREFIX` | `outputs` | 卷上产物目录前缀 |
| `S3_URL_TTL_S` | `3600` | 预签名 URL 有效期 |
| `WORKFLOWS_DIR` | `/opt/workflows` | 模板根目录（有网络卷时 entrypoint 会指向卷） |
| `VOL_ROOT` / `MANIFEST` | `/runpod-volume` / 脚本同目录清单 | `populate-volume.sh` 的卷与清单位置 |

## 模型

模型全部在卷上（`/runpod-volume/models`），镜像里只有空目录。清单格式是
`<子目录><TAB><文件名><TAB><下载地址>`，`populate-volume.sh` 逐条幂等下载，
Hugging Face 的地址优先走 `hf download`，其余走 `wget --continue`。

当前清单包含：Comfy-Org 的 H3 INT8 权重与 VAE、Singularity ref2va int8、
Qwen3-VL 32B NVFP4 文本编码器、lightx2v turbo LoRA、风格 LoRA、face detailer 用的
bbox / sam 权重，以及两个预编译的 TensorRT 引擎。ComfyUI 的模型目录通过
`extra_model_paths.yaml` 指到卷上，TensorRT 引擎目录是软链（构建一次、所有 worker 复用）。

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
IMG=codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0

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

- 增强集的 RIFE / 超分节点用的是上游版本，它们认的模型名与卷里预编译引擎不一致，
  所以那两个节点首次运行会各自编译引擎（几分钟，一次）；想直接复用预编译引擎需要
  换成产出这些引擎的节点版本。
- 模板导出时的 widget 会随节点版本漂移（例如 `crf` 被挪进 format 控件、`2K` vs `2k`）。
  handler 在投递前按运行中服务器的 schema 校正：只差大小写的值做归一，节点不认识的
  widget 丢弃走默认值，改动明细放在响应的 `widget_repairs` 里。
- 只有 `linux/amd64`；未在非 Blackwell 卡上验证。
