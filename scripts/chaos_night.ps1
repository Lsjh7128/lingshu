﻿# scripts/chaos_night.ps1 —— LINGSHU 混沌工程夜间脚本（A5）
#
# 设计要点（踩过的坑都写在这）：
#   1. 数据目录隔离：插件日志一律写 <repo>\.chaos-data（LINGSHU_DATA_DIR），
#      绝不写 %DSH_HOME%\plugins\dsh-lingshu —— 真实流量与自动压测混在一起会污染标定。
#   2. 相变可达：LINGSHU_CONTEXT_WINDOW=6000 把上下文窗口压小，
#      否则 1M 窗口下 70%/85% 在短会话里永远够不到，host 档等于白跑。
#   3. host 档只在 02:00–05:00 且实验 profile 存在时开；每次最多 3 次模型调用（run_chaos 内限流）。
#   4. 所有输出落 acceptance\chaos\logs\，失败也留日志（夜间无人值守，日志是唯一证据）。
#
# 兼容 Windows PowerShell 5.1（schtasks 默认用它启动）：所有语句单行，
# 不用 PS7 才支持的行尾二元运算符续行。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\chaos_night.ps1
#   powershell ... -File scripts\chaos_night.ps1 -DryRun        # 干跑：只跑 engine，绝不打模型
#   powershell ... -File scripts\chaos_night.ps1 -NoHost        # 强制跳过 host 档
#
# 退出码：0 = 全部成功；非 0 = 至少一档失败（engine 失败优先返回它的码）。

param(
    [switch]$DryRun,
    [switch]$NoHost,
    [switch]$SkipCalibrate,
    [int]$HostMaxModelCalls = 3
)

$ErrorActionPreference = "Continue"

# ── 路径 ────────────────────────────────────────────────
$repo = Split-Path -Parent $PSScriptRoot
$py = "$(if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' })\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$node = "$(if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' })\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
if (-not (Test-Path $node)) { $node = "node" }

$dataDir = Join-Path $repo ".chaos-data"
$logDir = Join-Path $repo "acceptance\chaos\logs"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $logDir ("chaos-night-" + $stamp + ".log")

function Log([string]$msg) {
    $line = "[" + (Get-Date -Format "HH:mm:ss") + "] " + $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Log "===== LINGSHU 混沌夜间任务开始 ====="
Log ("repo       : " + $repo)
Log ("python     : " + $py)
Log ("node       : " + $node)
Log ("data dir   : " + $dataDir + "（隔离；不碰真实插件数据目录）")
Log ("dry-run    : " + $DryRun + " | no-host: " + $NoHost + " | skip-calibrate: " + $SkipCalibrate)

# ── 环境：数据目录隔离 + 相变窗口压小 ────────────────────
$env:PYTHONIOENCODING = "utf-8"
$env:LINGSHU_DATA_DIR = $dataDir
$env:LINGSHU_CONTEXT_WINDOW = "6000"
$env:LINGSHU_NODE = $node

$runner = Join-Path $repo "tools\run_chaos.py"
$engineExit = 0
$hostExit = 0

# ── 1) engine 档：确定性、零模型调用，每天必跑 ────────────
if (Test-Path $runner) {
    Log "engine 档开始（确定性，零模型调用）"
    $out = & $py $runner --mode engine 2>&1
    $engineExit = $LASTEXITCODE
    foreach ($line in $out) { Log ("  " + [string]$line) }
    Log ("engine 档退出码 = " + $engineExit)
} else {
    Log ("!! 缺少执行器：" + $runner)
    $engineExit = 2
}

# ── 2) host 档：02:00–05:00 且实验 profile 存在才开 ──────
$dshHome = $env:DSH_HOME
if (-not $dshHome -or -not (Test-Path $dshHome)) { $dshHome = Join-Path $env:USERPROFILE ".dsh" }
$labBin = Join-Path $dshHome "profiles\node_modules\@deepseek-ai\dsh\lib\bin.js"
$hour = (Get-Date).Hour
$inWindow = ($hour -ge 2 -and $hour -lt 5)
$labOk = Test-Path $labBin
$hostAllowed = (-not $DryRun) -and (-not $NoHost) -and $inWindow -and $labOk
Log ("host 前置条件：dry-run=" + $DryRun + " no-host=" + $NoHost + " 当前小时=" + $hour + " 在02-05窗口=" + $inWindow + " profile存在=" + $labOk)

if ($hostAllowed) {
    Log ("host 档开始（真实实验 profile，模型调用上限 " + $HostMaxModelCalls + " 次/夜）")
    $out = & $py $runner --mode host --max-model-calls $HostMaxModelCalls 2>&1
    $hostExit = $LASTEXITCODE
    foreach ($line in $out) { Log ("  " + [string]$line) }
    Log ("host 档退出码 = " + $hostExit)
} else {
    $why = "不在 02:00-05:00 窗口"
    if ($DryRun) { $why = "dry-run" }
    elseif ($NoHost) { $why = "-NoHost" }
    elseif (-not $labOk) { $why = "实验 profile 不存在（" + $labBin + "）" }
    Log ("host 档跳过：原因=" + $why + "（未消耗任何模型调用）")
}

# ── 3) A2 自动标定：工具尚不存在时跳过并记录 ─────────────
$calibrate = Join-Path $repo "tools\chaos_calibrate.py"
if ($SkipCalibrate) {
    Log "标定跳过：-SkipCalibrate"
} elseif ($DryRun) {
    Log "标定跳过：dry-run 不执行 --apply（避免改配置）"
} elseif (Test-Path $calibrate) {
    Log "调用 A2 自动标定：tools\chaos_calibrate.py --apply"
    $out = & $py $calibrate --apply 2>&1
    $calExit = $LASTEXITCODE
    foreach ($line in $out) { Log ("  " + [string]$line) }
    Log ("标定退出码 = " + $calExit)
} else {
    Log "标定跳过：tools\chaos_calibrate.py 尚不存在（待 A2 交付后自动接管）"
}

# ── 4) 日志保留：只留最近 30 份，避免 logs 无限膨胀 ──────
try {
    $old = Get-ChildItem $logDir -Filter "chaos-night-*.log" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30
    foreach ($f in $old) { Remove-Item -Force -ErrorAction SilentlyContinue $f.FullName }
} catch { }

Log "===== 混沌夜间任务结束 ====="
Log ("汇总：engine=" + $engineExit + " | host=" + $hostExit)

if ($engineExit -ne 0) { exit $engineExit }
if ($hostExit -ne 0) { exit $hostExit }
exit 0
