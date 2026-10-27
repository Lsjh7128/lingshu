﻿# scripts/chaos_v2_night.ps1 —— LINGSHU 混沌工程 v2 夜间脚本（docs/23）
#
# 与 v1 的 scripts/chaos_night.ps1 **完全独立**（v1 四剧本与调度一行不改）：
#   · 只跑 v2 campaign（8 族 / 20 条不变量），**engine 档、零模型调用、零网络**；
#   · 不碰 .chaos-data*、不碰实验 profile、不碰 desktop profile；
#   · 唯一写盘范围：acceptance\chaos\v2\（产物 + 本脚本日志）。
#
# 设计要点（都是踩过的坑）：
#   1. 先 `gen_chaos_v2.py --check`：语料 = 生成物，被手改就**别跑**（判定基线已变）；
#   2. 再 `validate.py`：封套/唯一性/severity/哈希，坏了直接退出 1；
#   3. campaign 退出码 0 = 20 条不变量全过；2 = 有不变量失败/异常（或 -Strict 下的 finding）；
#   4. 夜里无人值守 → 所有输出落日志，失败也留（日志是唯一证据）；
#   5. **运行期间不要编辑仓库文件**：并发编辑会被 INV-19 当"未归因的快照差分"披露
#      （默认不判失败；-Strict 下会判失败）。
#
# 兼容 Windows PowerShell 5.1（schtasks 默认用它启动）：语句保持单行。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\chaos_v2_night.ps1
#   powershell ... -File scripts\chaos_v2_night.ps1 -DryRun          # 干跑：只打印计划，不写任何文件
#   powershell ... -File scripts\chaos_v2_night.ps1 -Strict          # CI：finding / 快照差分也判失败
#   powershell ... -File scripts\chaos_v2_night.ps1 -TimeoutSeconds 60
#
# 退出码：0 = 通过；2 = campaign 不变量失败/异常；1 = 用法或语料结构性错误。

param(
    [switch]$DryRun,
    [switch]$Strict,
    [int]$TimeoutSeconds = 90,
    [string]$RunId = "",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Continue"

# ── 路径 ────────────────────────────────────────────────
$repo = Split-Path -Parent $PSScriptRoot
$py = "$(if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' })\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$gen = Join-Path $repo "tools\gen_chaos_v2.py"
$validator = Join-Path $repo "chaos-set\v2\validate.py"
$campaign = Join-Path $repo "tools\run_chaos_campaign.py"
$manifest = Join-Path $repo "chaos-set\v2\manifest.json"
if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = Join-Path $repo "acceptance\chaos\v2" }
$logDir = Join-Path $OutDir "logs"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $logDir ("chaos-v2-night-" + $stamp + ".log")

function Log([string]$msg) {
    $line = "[" + (Get-Date -Format "HH:mm:ss") + "] " + $msg
    Write-Host $line
    if (-not $DryRun) { Add-Content -Path $logFile -Value $line -Encoding UTF8 }
}

# ── 计划（DryRun 只打印，不建目录、不写产物）─────────────
$campaignArgs = @($campaign, "--timeout-seconds", [string]$TimeoutSeconds)
if (-not [string]::IsNullOrWhiteSpace($RunId)) { $campaignArgs += @("--run-id", $RunId) }
if (-not [string]::IsNullOrWhiteSpace($OutDir)) { $campaignArgs += @("--out-dir", $OutDir) }
if ($Strict) { $campaignArgs += @("--strict-findings", "--strict-write-scope") }

if ($DryRun) {
    Write-Host "===== LINGSHU 混沌 v2 夜间任务（DryRun：只打印计划）====="
    Write-Host ("repo       : " + $repo)
    Write-Host ("python     : " + $py)
    Write-Host ("out dir    : " + $OutDir)
    Write-Host ("strict     : " + $Strict)
    Write-Host ""
    Write-Host ("[1] " + $py + " " + $gen + " --check")
    Write-Host ("[2] " + $py + " " + $validator)
    Write-Host ("[3] " + $py + " " + ($campaignArgs -join " "))
    Write-Host ("[4] 读取 " + (Join-Path $OutDir ".last.json") + " 打印摘要")
    Write-Host ""
    Write-Host "DryRun 未写任何文件；去掉 -DryRun 即执行（engine 档零模型调用）。"
    exit 0
}

# ── 前置检查 ────────────────────────────────────────────
if (-not (Test-Path $manifest)) {
    Write-Host ("!! 缺少 manifest：" + $manifest)
    exit 1
}
if (-not (Test-Path $campaign)) {
    Write-Host ("!! 缺少执行器：" + $campaign)
    exit 1
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
try { New-Item -ItemType Directory -Force -Path $logDir | Out-Null } catch { }

$env:PYTHONIOENCODING = "utf-8"

Log "===== LINGSHU 混沌 v2 夜间任务开始 ====="
Log ("repo       : " + $repo)
Log ("python     : " + $py)
Log ("out dir    : " + $OutDir + "（唯一写盘范围；零模型调用）")
Log ("strict     : " + $Strict + " | timeout: " + $TimeoutSeconds + "s")

# ── 1) 语料冻结：--check ────────────────────────────────
$exit1 = 0
if (Test-Path $gen) {
    Log "步骤 1/4：语料 = 生成物，检查是否被手改（gen_chaos_v2.py --check）"
    $out = & $py $gen --check 2>&1
    $exit1 = $LASTEXITCODE
    foreach ($line in $out) { Log ("  " + [string]$line) }
    Log ("  退出码 = " + $exit1)
} else {
    Log ("!! 缺少生成器：" + $gen)
    $exit1 = 1
}
if ($exit1 -ne 0) {
    Log "!! 语料被手改 / 生成器失败 → 停止（判定基线已变，不许强行跑）"
    exit 1
}

# ── 2) 语料校验：validate.py ────────────────────────────
$exit2 = 0
if (Test-Path $validator) {
    Log "步骤 2/4：行封套/唯一性/severity/哈希（validate.py）"
    $out = & $py $validator 2>&1
    $exit2 = $LASTEXITCODE
    foreach ($line in $out) { Log ("  " + [string]$line) }
    Log ("  退出码 = " + $exit2)
} else {
    Log ("!! 缺少校验器：" + $validator)
    $exit2 = 1
}
if ($exit2 -ne 0) {
    Log "!! 语料结构性错误 → 停止"
    exit 1
}

# ── 3) campaign ────────────────────────────────────────
Log "步骤 3/4：v2 campaign（8 族 / R=3 / 20 条不变量）"
$out = & $py $campaignArgs 2>&1
$exit3 = $LASTEXITCODE
foreach ($line in $out) { Log ("  " + [string]$line) }
Log ("  退出码 = " + $exit3)

# ── 4) 摘要（从 .last.json 读，不重新跑）─────────────────
$lastPath = Join-Path $OutDir ".last.json"
if (Test-Path $lastPath) {
    Log "步骤 4/4：产物摘要（.last.json）"
    try {
        $last = Get-Content -Path $lastPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $s = $last.summary
        Log ("  run_id   : " + $last.run_id)
        Log ("  verdict  : " + $s.verdict)
        Log ("  不变量   : " + $s.invariants_passed + "/" + $s.invariants_total + "（flaky " + $s.invariants_flaky + "）")
        Log ("  检查     : cases=" + $s.checks_total + " failed=" + $s.checks_failed)
        Log ("  findings : " + $s.findings_total + " 条产品缺陷（见 acceptance\chaos\v2\findings.md）")
        Log ("  报告     : " + (Join-Path $OutDir ((Get-Date -Format "yyyy-MM-dd") + "-report.md")))
    } catch {
        Log ("  !! .last.json 解析失败：" + $_.Exception.Message)
    }
} else {
    Log ("  !! 未找到 .last.json（campaign 可能未写出产物）：" + $lastPath)
}

Log ("===== 结束：exit=" + $exit3 + " =====")
exit $exit3
