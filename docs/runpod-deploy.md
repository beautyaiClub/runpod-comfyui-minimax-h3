# RunPod 部署备忘

## 部署方式

本项目使用 RunPod 的 GitHub 集成构建，不依赖 Docker Hub：

| 配置 | 值 |
| --- | --- |
| GitHub 仓库 | `beautyaiClub/runpod-comfyui-minimax-h3` |
| 分支 | `main` |
| Dockerfile | `/.runpod/Dockerfile` |
| 构建方式 | RunPod builder 在 Dockerfile 构建阶段下载并打包模型 |
| 模型卷 | 不需要 Global Volume 或 Network Volume |
| 模型清单 | `models-manifest.txt`，14 个文件，约 77.6GB |
| 运行镜像 | RunPod 内部构建产物，不推送 Docker Hub |

`.runpod/Dockerfile` 是完整 Dockerfile，不是 `FROM codoive/...` 的镜像包装层。
它使用 `ubuntu:24.04`、CUDA 13.0、ComfyUI v0.40.0 和 CUDA 13.0 PyTorch，
并在 `BAKE_MODELS=true` 时把模型下载到 `/opt/comfyui-baked/models`。

## 构建流程

1. 将 Dockerfile 和代码提交到 GitHub `main`。
2. RunPod GitHub builder 检测新的 commit。
3. Builder 安装 ComfyUI、节点和 Python 依赖。
4. Builder 按 `models-manifest.txt` 下载 14 个模型文件。
5. RunPod 保存构建产物，Endpoint 使用该内部镜像。
6. Worker 启动时直接从镜像读取模型，不执行模型下载。

模型构建失败时，不要把不完整的 `.part` 文件当成完成文件。Dockerfile 只在下载成功
后才将 `.part` 重命名为最终文件。

## Endpoint 目标配置

- `workers.min = 0`
- `workers.max = 1`
- `idleTimeout = 5`
- `executionTimeout = 3600s`
- GPU 池：`AMPERE_80`、`BLACKWELL_96`
- CUDA：`13.0`
- 容器盘：30GB 运行期临时空间
- 不配置 `networkVolumeIds`
- 不配置 `volumes`

模型已经在镜像层内，不能再让启动脚本把 77GB 模型复制到 `/workspace`，否则会产生一次
不必要的冷启动复制。`serverless-entrypoint.sh` 检测到 `BAKED_MODELS=true` 后直接使用
`/opt/comfyui-baked`；只有代码自检镜像或旧卷部署才会走复制兼容路径。

## 成本口径

- 没有 Global Volume 的固定存储费。
- 没有 Network Volume 的固定存储费。
- RunPod 构建产物和缓存由 RunPod 管理，不是 Docker Hub 存储。
- Worker 从启动到停止按秒计费，包括冷启动、任务执行和 `idleTimeout` 等待时间。
- `workers.max=0` 时没有 Worker 运行，不产生 GPU Worker 运行费用。
- 大镜像会增加首次冷启动准备时间，但 RunPod 会在 scaled-down 阶段准备镜像，准备阶段不等于
  Worker 正在执行任务。

## 两套模板

模板直接放在镜像中：

```
/opt/workflows/native/MiniMaxH3-Native-FL2VA.json
/opt/workflows/native/MiniMaxH3-Native-Ref2VA.json
/opt/workflows/enhancers/*.json
```

通过 `template_set` 或端点默认环境变量 `H3_TEMPLATE_SET` 选择：

- `native`：原生 FL2VA / Ref2VA，不启用增强节点；
- `enhancers`：启用提示词增强、RIFE、TensorRT 超分和 face detailer。

修改模板需要提交 GitHub 并触发一次新的 RunPod 构建。旧的卷模板 seed 逻辑只为兼容旧卷保留，
新的 image-only Endpoint 不需要它。

## 模型

`models-manifest.txt` 是唯一模型清单。Dockerfile 构建时将文件下载到：

```
/opt/comfyui-baked/models/
```

当前模型包括 H3 INT8 diffusion models、Singularity ref2va、Qwen3-VL NVFP4 text encoder、
Turbo LoRA、风格 LoRA、face detector、SAM、VAE 和 QwenVL enhancer 的 GGUF 文件。
TensorRT 引擎不打包，增强节点首次使用时在 Worker 本地编译；引擎与 GPU 架构相关，不能跨
不同 GPU 池直接复用。

## 探活和真实任务

```bash
curl -s -X POST "https://api.runpod.ai/v2/<endpointId>/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"input":{"action":"health"}}'
```

健康检查应返回 `status=ok`，并显示 `/opt/workflows`。它不需要模型卷，也不执行模型下载。

真实任务使用 `/run`，长任务轮询 `/status/<jobId>`。小文件由 handler 直接返回，大文件按请求
中的 `s3Config` 走 S3 预签名 URL。

## 排障

| 现象 | 原因 / 处理 |
| --- | --- |
| Build 长时间不动 | 正在下载约 77.6GB 模型或解析 Python 依赖；看 RunPod builder 的 plain 日志，不要中断构建 |
| Worker 反复重启 | 检查 Dockerfile 最后对 shell 脚本做的 CRLF 清理和 shebang 断言 |
| Worker 启动很慢 | 首次需要准备约 90～95GB 的内部镜像；不要再在 entrypoint 中下载或复制模型 |
| 任务找不到模型 | 检查镜像构建是否包含 14 个模型文件，以及 `BAKED_MODELS=true` 是否生效 |
| 任务一直 IN_QUEUE | 当前 GPU 池没有容量；切换允许的 GPU 池，不要通过模型卷解决 |
| 增强节点失败 | 检查 TensorRT 版本、GPU 架构和节点版本是否匹配；native 模板先单独验证 |

## 回退方案

新镜像通过 `health`、native FL2VA 和 native Ref2VA 验证前，不删除原来的 Global Volume。
验证成功后再按顺序执行：

1. Endpoint 切换为新的 RunPod GitHub 构建产物；
2. 清空 `volumes` 和 `networkVolumeIds`；
3. 确认新 Worker 在没有卷的情况下完成健康检查和真实任务；
4. 再删除旧 Global Volume，停止固定存储费用。

## 接入 MyReels（Generator + Gateway）

Gateway 侧**已经有** RunPod 通道，不需要新端点：

```
api_endpoints.id = b618713f-e907-477c-b831-9827c8b97f93
  path   = /v2/**        description = Runpod自定义工作流
  billing_config = {"billingType": "expression",
                    "expression": "$exists($responseRaw.executionTime) and $exists($response.data.widget_repairs)
                                   ? ($responseRaw.executionTime + ($exists($responseRaw.delayTime) ? $responseRaw.delayTime : 0)) / 1000 * 0.000761 * 7
                                   : ($response.data.executionTime / 1000 * 0.00034 * 7)",
                    "billingKeyExpression": "$response.id",
                    "billingTriggerExpression": "$response.status = 'success'"}
  enable_transform = 1，request_transform_config 里有按 runpodType 生成 {"input": {...}} 的 JSONata 规则
凭据：runpod_api_key（已存在凭据服务里）
```

这个 `billing_config` 就是「实际计费」的来源（Gateway 把它塞进 `X-Gateway-Billing-Cost` 响应头，
Generator 的 runpodWorker 读到后写进任务 `actualCost`）。几点说明：

- 计费只在 status 轮询返回 `success`（即 RunPod `COMPLETED`）的那一次触发，key 用 RunPod job id 去重；
- 单价 `0.000761 $/s × 7` = A100 80GB 的 serverless 价（按 3.49/h 的 Blackwell 跑会低估约 28%）；
- 用 `widget_repairs` 这个只有本 worker 才会返回的字段区分服务：其它走同一通道的 runpod 工作流
  行为完全不变（表达式仍算 0，和改动前一致）；
- 为什么不用 `$response.data.executionTime`：Gateway 的 `responseTransformConfig` 只在
  `$.output.video` 存在时才会把 executionTime 映射进 data，我们的响应是 `data.images[]`，
  所以要用 `$responseRaw`（未转换的 RunPod 原始响应）+ `delayTime` 才拿得到真实的启动+执行时间。

Generator 侧走一等公民链路：`TaskJobType.RUNPOD` → `workers/runpod/runpodWorker.ts`

- `submitToAPI`：`POST {gateway_url}`，`:instance` 用 **ServiceConfig.model_desc** 替换
- `buildRequestBody`：直接把任务的 `merged_prompt` 当请求体（所以 prompt 模板本身要产出 `{"input": {...}}` 形状，或由 Gateway 的 request transform 包一层）
- `pollRunpod`：用同一 `gateway_url` 的 status 路径，`:instance` 同样替换
- `extractResultAssets`：只认 `data.video_url` / `data.image_url` / `data.audio_url`，以及 `data.images[] = {data: <base64>, type: "base64", filename}`

所以要让 H3 worker 接进去，需要三步：

1. **worker 响应形状**（已在本仓库 `handler.py::_result_payload` 实现）：
   除了原来的 `outputs/texts/widget_repairs`，再镜像出 `video_url` / `image_url` / `audio_url`
   和 `images[].{data,type:'base64',filename}`，正好落在 Generator 读取的 `data.*` 上。
2. **Generator 的 `service_config` 加一行**（`model_name` 是模型名，`model_desc` 填 RunPod endpoint id）：

   | 列 | 值 |
   | --- | --- |
   | model_name | `myreels-minimax-h3`（自定义，id `43bc24f1-b62d-42e1-bbcc-32bbb3c17220`） |
   | model_desc | RunPod endpoint id = `3w2llhe9btnsmn`（换端点只改这一列） |
   | cost_price / estimated_price / estimated_generation_time / profit_margin | 见下表（按实测成本折算） |
   | gateway_method / gateway_url | `POST` / `/p/runpod-api/v2/:instance/run` |
   | response_handling | `{"mode": "auto"}` |
   | is_active / access_tiers / is_api_enabled | `1` / `["normal"]` / `1` |

   定价值是从实测反推的：A100 80GB 冷启动一次 5s/native 的真实成本约 ¥1.23（`actualCost` 会按
   profit_margin 再放大成用户看到的数），估算价按"默认分辨率 768p + 冷启动"留了余量：

   | 场景 | 实测 actualCost | 说明 |
   | --- | --- | --- |
   | 5s / 480p / native / 冷启动 | ¥1.3626（Gateway 原值 ¥1.2331） | processingTime 237s |
   | 5s / 480p / native / 热跑 | —— | processingTime 170s，Gateway 原值 ¥0.861 |

   参照现有行：`myreels-infinite-avatar | vuvb9fz9evmu1h | … | POST | /p/runpod-api/v2/:instance/run`。
3. **`service_prompt_resources` 加一条该模型的 prompt 资源**，`base_config` 就是发给 worker 的 body，例如：

   ```json
   {"input": {"workflow": "MiniMaxH3-Native-Ref2VA.json", "template_set": "native",
              "prompt": "{{prompt}}", "config": "native_turbo", "seconds": "{{seconds}}",
              "images": "{{images}}", "audios": "{{audios}}", "model": "myreels-minimax-h3"}}
   ```

   同时在 `myreels-config/model_manifest/by_gateway/` 里补一行
   `<模型名> | /p/runpod-api/v2/:instance/run`（现有文件 `28_p_runpod-api_v2_instance_run.txt`）。

以上 2、3 两步要写 Generator 的 MySQL（`myreels-config` 只是快照，改了不会自动生效），
需要对应的库访问权限或由你在管理端导入。

## GitHub Endpoint 与 RunPod Hub

生产部署不需要上架 Hub，也不需要 GitHub Release。私有 Endpoint 直接关联仓库的 `main` 分支，
RunPod 根据 `/.runpod/Dockerfile` 构建内部镜像即可。

`.runpod/hub.json` 和 `.runpod/tests.json` 只在以后选择上架 Hub 时使用；它们不是私有 GitHub
Endpoint 的必要条件。即使上架 Hub，Hub 也应使用这份完整 Dockerfile，不需要 Docker Hub 镜像。

`.runpod/tests.json` 当前只跑 `{"action":"health"}`，健康检查不依赖模型卷、不下载模型。真实
FL2VA / Ref2VA 仍应在新内部镜像构建成功后通过 Endpoint 单独验证。
