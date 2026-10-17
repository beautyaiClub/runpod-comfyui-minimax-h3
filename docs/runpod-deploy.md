# RunPod 部署备忘

## 当前生产部署

| 项目 | 当前值 |
| --- | --- |
| Endpoint | `3w2llhe9btnsmn` / `myreels-minimax-h3` |
| GitHub | `beautyaiClub/runpod-comfyui-minimax-h3`，分支 `main` |
| 当前生产镜像 | RunPod 内部构建镜像 `b71517eaa` |
| 模型卷 | Network Volume `icf8p97vyb` / `myreels-h3-models` |
| 机房 | `US-MD-1` |
| 卷大小 | 160GB |
| 挂载路径 | `/runpod-volume` |
| GPU 池 | `AMPERE_80`、`BLACKWELL_96` |
| Worker | `min=0`、`max=1`、`idleTimeout=5` |

当前 Endpoint 已通过 API 切回 Network Volume：

```json
{
  "mounts": {
    "global": [],
    "network": [{"volumeId": "icf8p97vyb"}]
  }
}
```

Global Volume 已删除，不再产生 Global Volume 存储费用；生产只保留 Network Volume。

## 为什么不用 Global Volume 生产推理

Global Volume 的模型文件总大小约 77.55GB，功能验证通过，但性能不合格：

| 方案 | GPU | 480p / 5s | 768p / 5s |
| --- | --- | ---: | ---: |
| Network Volume 历史实测 | A100 80GB | execution 约 131～145秒 | 约 2～4分钟量级 |
| Global Volume 本轮实测 | RTX PRO 6000 Blackwell | 1,283.9秒，约21.4分钟 | 1,199.2秒，约20.0分钟 |
| Network Volume 回切后实测 | A100 80GB | 144.4秒，约2.4分钟 | — |

Global 的 480p 和 768p 执行时间几乎一样，说明瓶颈不是分辨率，而是模型在对象存储
FUSE 路径上的加载/随机读取。Global Volume 适合跨机房共享模型或数据集，不适合 ComfyUI
这类需要频繁随机读取大型 safetensors 的推理服务。

## 卷内容

```text
/runpod-volume/models/...            14 个 H3 模型文件，约 77.55GB
/runpod-volume/workflows/native/     MiniMaxH3-Native-FL2VA.json / -Ref2VA.json
/runpod-volume/workflows/enhancers/  5 个增强模板
```

生产环境变量：

```text
H3_TEMPLATE_SET=native
WORKFLOW_SEED=true
VOLUME_AUTOPOPULATE=false
JOB_TIMEOUT_S=1800
```

模型和模板已经预灌到 Network Volume，worker 不应在 GPU 计费期间下载模型。

## 成本

```text
Network Volume：160GB × $0.07/GB/月 ≈ $11.20/月
Container disk：30GB，另计
```

Network Volume 是当前唯一的生产模型卷。`workers.max=0` 时没有运行中的 worker，不产生 GPU
worker 运行费用；当前 Endpoint 已暂停，提交 Generator 任务前需要将 `max` 恢复为 1。

## 健康检查与真实任务

```bash
curl -s -X POST "https://api.runpod.ai/v2/3w2llhe9btnsmn/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"input":{"action":"health"}}'
```

健康检查应返回：

```json
{
  "status": "ok",
  "volume_mounted": true,
  "workflows_dir": "/runpod-volume/workflows"
}
```

本轮 Network Volume 回切后的 health 已成功，随后 480p native FL2VA 也成功：

```text
status=COMPLETED
executionTime=144366ms
workerId=wxp8dw28xez05o
```

## 排障

| 现象 | 原因 / 处理 |
| --- | --- |
| 任务一直 IN_QUEUE | `US-MD-1` 的 GPU 没有容量；Network Volume 与机房绑定，不能随意换机房 |
| worker 启动但找不到模板 | 检查 `/runpod-volume/workflows/native` 和 `/runpod-volume/workflows/enhancers` |
| worker 启动但找不到模型 | 检查 `VOLUME_AUTOPOPULATE=false` 时卷是否已经完整预灌 |
| 模型读取很慢 | 不要重新使用 Global Volume；H3 safetensors 生产读取使用 Network Volume |
| 冷启动时间较长 | 模型加载在 worker 启动/首次任务期间发生；不要把模型下载放进 GPU worker |

## 卷清理状态

- Network Volume `icf8p97vyb` 当前正在使用，不能删除。
- Global Volume 已删除；当前没有 Global Volume 回退副本。
- 删除 Network Volume 前必须先创建并完整验证新的模型存储方案。

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
