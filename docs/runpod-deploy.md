# RunPod 部署备忘

## 当前资源

| 资源 | id | 关键配置 |
| --- | --- | --- |
| 端点 | `3w2llhe9btnsmn` | `myreels-minimax-h3`，**GitHub 集成**（repo `beautyaiClub/runpod-comfyui-minimax-h3`、branch `main`、`dockerFilePath = /.runpod/Dockerfile`），workers `{min:0,max:1,idleTimeout:5}`，executionTimeout 60min，CUDA ≥13.0 |
| 模板 | `jx2vv4datz`（REST 建）/ `jk9p6dnxf8`（GitHub 集成用） | 镜像 `codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0`，容器盘 30GB，serverless |
| 网络卷 | `icf8p97vyb` | 160GB，`US-MD-1`（卷与机房绑定，端点只能在该机房起 worker） |
| GPU 池 | — | `AMPERE_80`（A100 80GB，$2.72/h）+ `BLACKWELL_96`（RTX PRO 6000 96GB，$3.49/h）——两档都实测跑通 H3 |
| 已删除 | ~~`q76qt1gteue38g`~~ | 早期用 REST 建的端点（控制台里没有 GitHub 图标那一个），2026-10-16 删掉 |

模板环境变量：`H3_TEMPLATE_SET=native`、`WORKFLOW_SEED=true`、`VOLUME_AUTOPOPULATE=false`、`JOB_TIMEOUT_S=1800`。

## 费用与「内存」问题

官方口径（`https://docs.runpod.io/serverless/pricing`）：**从 worker 启动到彻底停止，按秒计费**，
计费区间 = 冷启动（模型加载）+ 执行（execution）+ 空闲等待（idleTimeout，默认 5s）；镜像拉取和
scaled-down 状态不计费。控制台里显示 **Idle = 已缩容 = 不计费**。存储另计：

```
容器盘   30GB × ~$0.10/GB/月 = $3/月
网络卷  160GB ×  $0.07/GB/月 = $11.20/月（<1TB 档）
```

**关于「内存」**：显存（80GB / 96GB / 180GB…）是卡型的属性，选定卡型后**改不了、也不单独计价**，
"把内存调小省钱"不存在——价格档位就是按卡型定的。能动的只有四件事：① 允许的卡型池
（已砍到最便宜且实测能跑的两档）；② 容器盘大小；③ 网络卷大小；④ 空闲 worker
（`workers.min=0`，不用时把 `workers.max` 设 0 就是完全停）。账号里那个「180G」是 B200 的显存，
B200/B300/H200/H100 都已从池子里移除。

serverless 单价（`GET https://api.runpod.io/v2/catalog/gpus` 的 `price.serverless`，2026-10-16）：

| 卡型 | pool | serverless $/h | ¥/s（按 7 折算） |
| --- | --- | --- | --- |
| A100-SXM4-80GB | `AMPERE_80` | 2.72 | 0.005294 |
| RTX PRO 6000 Blackwell 96GB | `BLACKWELL_96` | 3.49 | 0.006794 |

实测一次生成（2026-10-16，A100 80GB / US-MD-1，5s / native / FL2VA）：

| 场景 | 启动等待 delayTime | 执行 executionTime | 合计 | 真实成本 |
| --- | --- | --- | --- | --- |
| worker 已热 | 17.0s | 144.7s | 161.7s | ¥0.86（$0.123） |
| 冷启动 | 100.0s | 131.4s | 231.4s | ¥1.23（$0.176） |

对账方法：`GET https://rest.runpod.io/v1/billing/endpoints` 会返回每个端点的
`amount` / `timeBilledMs`（A100 实测 `$0.0486 / 63.864s` ≈ `$0.000761/s`，即 $2.74/h，与官方价一致），
把 `Σ(delayTime + executionTime)` 和它比一下就知道有没有算漏。冷启动那次 delay 100s 说明
**worker 启动是计费的**（拉镜像不计），所以池子里别放"边跑边下模型"的配置。

日常流程（`max=1` 时闲置会按秒计费，A100 约 $0.045/分钟）：

```powershell
pwsh scripts/runpod-endpoint.ps1 status     # 看 max / 当前 worker
pwsh scripts/runpod-endpoint.ps1 activate   # 生成前：max=1
pwsh scripts/runpod-endpoint.ps1 health     # 探活（会冷启动）
pwsh scripts/runpod-endpoint.ps1 pause      # 生成后：max=0，0 worker = 0 费用
```

暂停期间 `/run` 会返回 `ENDPOINT_PAUSED`（409），这是预期行为；接了 Generator 要无人值守就保持
`max=1`（页面上的任务才跑得通），代价只有"任务之间那几秒到几分钟"的空闲。
注意 v2 的 `GET /v2/serverless/<id>/workers` 里那些 THROTTLED/IDLE 记录会残留十几分钟，
但容器其实已经 `stop container … exit code 137` 了（worker 日志可证），不是活跃计费状态。

## 卷上的内容

```
/runpod-volume/models/...            按 models-manifest.txt 下载的权重（约 100GB）
/runpod-volume/workflows/native/     MiniMaxH3-Native-FL2VA.json / -Ref2VA.json
/runpod-volume/workflows/enhancers/  5 个增强模板
```

`WORKFLOW_SEED=true` 只在目录不存在时从镜像复制一次，之后改模板只需换文件 + 重启 worker。

## 灌模型：用便宜机器，别用 GPU worker

开机时 `VOLUME_AUTOPOPULATE=true` 会让 GPU worker 边跑边下 100GB（96GB 卡按小时计费，纯浪费）。
正确做法是先用一台 CPU pod 把卷灌满（实测 6 分钟、约 $0.12/小时的机型，成本不到 2 分钱），
再把 GPU worker 的 `VOLUME_AUTOPOPULATE` 设为 `false`，这样它开机只做 ComfyUI 材料化和模板 seed，1-2 分钟就能接活。

```bash
RUNPOD_API_KEY=$(cat .build/runpod-key.txt)

# 1) 便宜的 CPU pod 跑 populate-volume.sh，跑完保持存活方便看日志
curl -s -X POST https://rest.runpod.io/v1/pods \
  -H "Authorization: Bearer $RUNPOD_API_KEY" -H 'Content-Type: application/json' \
  -d '{
        "name": "h3-volume-populate",
        "computeType": "CPU",
        "vcpuCount": 4,
        "imageName": "codoive/runpod-minimax-h3:cu130-comfyui-v0.40.0",
        "containerDiskInGb": 30,
        "networkVolumeId": "icf8p97vyb",
        "volumeMountPath": "/runpod-volume",
        "dataCenterIds": ["US-MD-1"],
        "dockerEntrypoint": ["/bin/bash", "-lc"],
        "dockerStartCmd": ["/opt/serverless/populate-volume.sh; echo POPULATE_EXIT=$?; sleep 604800"]
      }'

# 2) 看进度（SSE），等出现 POPULATE_EXIT=0
curl -s -N "https://api.runpod.io/v2/pods/<podId>/logs" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" -H 'Accept: text/event-stream'

# 3) 删掉 pod
curl -s -X DELETE "https://rest.runpod.io/v1/pods/<podId>" -H "Authorization: Bearer $RUNPOD_API_KEY"
```

populate 幂等：已存在的文件跳过，HF 下载走临时目录再落盘，中断后重跑不会留半截文件。

## 探活

```bash
curl -s -X POST "https://api.runpod.ai/v2/htopp3qrarr54p/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" -H 'Content-Type: application/json' \
  -d '{"input":{"action":"health"}}'
```

期望：`{"status":"ok","volume_mounted":true,"workflow_sets":{"native":[...],"enhancers":[...]},"workflows_dir":"/runpod-volume/workflows"}`。

`/runsync` 会等 worker 起来（冷启动 + 排队），长任务用 `/run` + 轮询 `/status/<jobId>`。

## 发真实任务

```bash
curl -s -X POST "https://api.runpod.ai/v2/htopp3qrarr54p/run" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" -H 'Content-Type: application/json' \
  -d '{
        "input": {
          "workflow": "MiniMaxH3-Native-Ref2VA.json",
          "template_set": "native",
          "prompt": "a woman walks through a neon alley, [DOLLY IN]",
          "config": "native_turbo",
          "seconds": 6,
          "images": ["https://…/ref1.png", "https://…/ref2.png"],
          "videos": ["https://…/ref.mp4"],
          "audios": ["https://…/ref.mp3"]
        }
      }'
```

字段说明见仓库根 README 的「Job 输入」一节。产物小文件直接 base64 回传，超过
`OUTPUT_INLINE_MAX_MB`（默认 10MB）且请求里带 `s3Config` 时走 S3 并返回预签名 URL。

## 排障

| 现象 | 原因 / 处理 |
| --- | --- |
| worker 反复重启、日志只有平台行、没有容器输出 | 镜像里的 shell 脚本是 CRLF（`#!/bin/bash\r`）→ exec 失败。`.gitattributes` 已固定 LF，Dockerfile 里还有一次 `sed` 兜底 + shebang 断言 |
| 任务一直 IN_QUEUE 且没有 worker | 该机房当前没有可用 GPU。查 `GET https://api.runpod.io/v2/serverless/<endpointId>/workers`，或用 `curl https://api.runpod.io/v2/catalog/gpus` 看全网余量；换机房需要重建卷（模型要重灌，用上面的 CPU pod 方法） |
| worker 起来但任务不动 | 看 worker 日志 `GET https://api.runpod.io/v2/serverless/<endpointId>/workers/<workerId>/logs`（SSE）。若卡在 populate，说明卷是空的且 `VOLUME_AUTOPOPULATE=true` |
| 冷启动很久 | 卷是空的（要下 100GB）→ 用 CPU pod 预灌；正常冷启动 1-2 分钟 |

## 成本参考

- 一次性：本地构建镜像 + 推 Docker Hub（免费层）；模型下载用 CPU pod（$0.12/小时，实测 6 分钟，< $0.02）
- 长期固定：网络卷 160GB = **$11.20/月** + 容器盘 30GB ≈ **$3/月**（容器盘只在 worker 存活时按 5 分钟粒度计）
- 每次生成：`(delayTime + executionTime) × 卡型单价`，A100 80GB 实测
  `5s / 480p / native` 冷启动 ¥1.23、热跑 ¥0.86；分辨率翻到 768p、时长翻倍会按采样步数近似线性增长
- 空闲：`workers.max=1` 时任务之间的空档按秒计费（A100 $0.045/分钟），长时间没人用就 `pause`

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

## 发布到 Runpod Hub（像其它 serverless 那样挂 GitHub 项目）

> 现状：**已经不用走 Hub 了**。这里记的是"上架 Hub"这条路；日常部署直接用 GitHub 集成端点
> `3w2llhe9btnsmn` 就够了 —— 它在仓库里读 `/.runpod/Dockerfile`（薄壳 `FROM` 我们推到 Docker Hub
> 的现成镜像），所以推送新的 commit 会触发重建，控制台里也会显示 GitHub 图标。

Hub 的要求（本仓库已经全部满足）：

1. 仓库里有 `handler.py`、`Dockerfile`、`README.md`（根目录或 `.runpod/` 下）
2. `.runpod/` 下有 `hub.json`（元数据 + 部署配置）和 `tests.json`（Hub 用它做上线自检）
3. **必须打一个 GitHub Release**（Hub 只索引 release，不看普通 commit）
4. 到 console.runpod.io 的 Hub 发布页填仓库地址；之后 Runpod 会构建/测试/人工审核上架
5. 以后更新：再发一个 release 即可，Hub 自动重新索引构建（约 1 小时）

差别在于 **Hub 会用仓库里的 Dockerfile 在 Runpod 的构建机上重新构建镜像**（这套依赖装起来
本机实测 30-60 分钟），而我们现在是本地构建好推到 Docker Hub 的现成镜像。两种可以并存：
Hub listing 负责"一键部署 + 从 GitHub 版本化"，日常部署仍可直接用现成镜像 tag。

`.runpod/tests.json` 现在只跑 `{"action":"health"}`（300s 超时），自检卡型是
`NVIDIA A100-SXM4-80GB`（GitHub 集成第一次构建就是用它跑 TESTING 阶段的：换成 96GB 的
Blackwell 时在 US-MD-1 排不到机器，会一直卡在 TESTING）。要让自检跑真实生成，
把超时调到 1800s 并在 `input` 里给一个完整任务即可。
