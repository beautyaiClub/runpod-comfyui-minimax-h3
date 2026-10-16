<#
Activate / pause the MyReels MiniMax-H3 serverless endpoint and talk to it.

Runpod serverless bills from worker start until it fully stops (cold start +
execution + idle timeout), so `pause` (workers.max = 0) is the only way to reach
zero cost. The endpoint is the GitHub-integration one
(`3w2llhe9btnsmn`, repo beautyaiClub/runpod-comfyui-minimax-h3 @ main,
`/.runpod/Dockerfile`); keep `max = 1` while the MyReels Generator is expected to
submit tasks, `pause` when nobody is generating.

  pwsh scripts/runpod-endpoint.ps1 status
  pwsh scripts/runpod-endpoint.ps1 activate
  pwsh scripts/runpod-endpoint.ps1 run -JsonFile .build/gen-job.json
  pwsh scripts/runpod-endpoint.ps1 wait  -JobId <jobId>
  pwsh scripts/runpod-endpoint.ps1 pause

The RunPod API key is read from .build/runpod-key.txt (gitignored).
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('status', 'activate', 'pause', 'health', 'run', 'wait')]
    [string]$Action,

    [string]$EndpointId = '3w2llhe9btnsmn',
    [string]$KeyFile = (Join-Path $PSScriptRoot '..\.build\runpod-key.txt'),
    [string]$JsonFile,
    [string]$JobId,
    [int]$TimeoutSeconds = 1800
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $KeyFile)) { throw "API key file not found: $KeyFile" }
$Key = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
$Auth = @{ Authorization = "Bearer $Key" }
$Json = @{ Authorization = "Bearer $Key"; 'Content-Type' = 'application/json' }
$Base = "https://api.runpod.io/v2/serverless/$EndpointId"
$Jobs = "https://api.runpod.ai/v2/$EndpointId"

function Get-Workers { (Invoke-RestMethod -Uri "$Base/workers" -Headers $Auth -TimeoutSec 60) }

switch ($Action) {
    'status' {
        $endpoint = Invoke-RestMethod -Uri $Base -Headers $Auth -TimeoutSec 60
        $workers = Get-Workers
        [pscustomobject]@{
            name            = $endpoint.name
            workersMin      = $endpoint.workers.min
            workersMax      = $endpoint.workers.max
            idleTimeout     = $endpoint.workers.idleTimeout
            pools           = ($endpoint.gpu.pools -join ',')
            flashboot       = $endpoint.flashboot
            containerDiskGb = $endpoint.disk
            workersNow      = $workers.summary.total
            workerStates    = (($workers.workers | ForEach-Object { "$($_.gpuTypeId):$($_.status)" }) -join ', ')
        } | Format-List
    }
    'activate' {
        Invoke-RestMethod -Method Patch -Uri $Base -Headers $Json -TimeoutSec 90 `
            -Body '{"workers":{"min":0,"max":1,"idleTimeout":5}}' | Out-Null
        'activated (workers.max=1) — GPU starts billing once a worker boots'
    }
    'pause' {
        Invoke-RestMethod -Method Patch -Uri $Base -Headers $Json -TimeoutSec 90 `
            -Body '{"workers":{"min":0,"max":0,"idleTimeout":5}}' | Out-Null
        Start-Sleep -Seconds 15
        "paused (workers.max=0) — workers now: $((Get-Workers).summary.total)"
    }
    'health' {
        $body = '{"input":{"action":"health"}}'
        Invoke-RestMethod -Method Post -Uri "$Jobs/runsync" -Headers $Json -Body $body -TimeoutSec $TimeoutSeconds
    }
    'run' {
        if (-not $JsonFile) { throw '-JsonFile <job body json> is required for run' }
        $result = Invoke-RestMethod -Method Post -Uri "$Jobs/run" -Headers $Json -Body (Get-Content -LiteralPath $JsonFile -Raw) -TimeoutSec 90
        $result | ConvertTo-Json -Depth 4
        "reminder: pwsh scripts/runpod-endpoint.ps1 pause   # when finished"
    }
    'wait' {
        if (-not $JobId) { throw '-JobId is required for wait' }
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            $status = Invoke-RestMethod -Uri "$Jobs/status/$JobId" -Headers $Auth -TimeoutSec 60
            "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $status.status
            if ($status.status -in @('COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT')) {
                $status | ConvertTo-Json -Depth 6 -Compress
                break
            }
            Start-Sleep -Seconds 15
        }
    }
}
