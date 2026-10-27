#Requires -Version 5.1
<#
.SYNOPSIS
  LINGSHU 跨环境控制台 · 一键启动（只读本地服务，默认 http://127.0.0.1:3000）。

.DESCRIPTION
  做四件事：
    1) 找到可用的 Python（优先 DSH 自带运行时，不要求系统 PATH）；
    2) 启动 console/server.py（独立进程，只读；console 崩了不影响 DSH）；
    3) 等 /api/health 就绪后打开浏览器；
    4) Ctrl+C 或关闭窗口时停掉本脚本启动的进程（并写 PID 文件供 console_stop.ps1 用）。

  不会写任何 profile 数据、不会安装任何东西、不会碰 desktop profile。
  desktop 接入是**单独的人工脚本**：scripts/console_desktop_onboard.ps1（默认 dry-run）。

.EXAMPLE
  .\scripts\console.ps1
  .\scripts\console.ps1 -Port 3001 -NoOpen
  .\scripts\console.ps1 -Check                 # 只校验注册表与数据源
  .\scripts\console.ps1 -Demo                  # 启动演示流量 + 控制台（合成数据）
  .\scripts\console.ps1 -Profile lingshu-lab   # 默认选中实验 profile
#>
param(
  [int]$Port = 3000,
  [string]$Profile = '',
  [switch]$NoOpen,
  [switch]$Check,
  [switch]$Demo,
  [int]$DemoTurns = 40,
  [int]$DemoIntervalMs = 1200,
  [string]$Registry = '',
  [string]$Python = '',
  [string]$Repo = '',
  [double]$Interval = 1.5
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Write-Step([string]$msg) { Write-Host "[console] $msg" }
function Write-Fail([string]$msg, [string]$hint = '') {
  Write-Host "[FAIL] $msg" -ForegroundColor Red
  if ($hint) { Write-Host "       提示：$hint" }
}

function Resolve-RepoRoot {
  if ($Repo) { return (Resolve-Path -LiteralPath $Repo).Path }
  return (Split-Path -Parent $PSScriptRoot)
}

function Find-Python {
  $candidates = New-Object System.Collections.Generic.List[string]
  if ($Python) { $candidates.Add($Python) }
  if ($env:LINGSHU_PYTHON) { $candidates.Add($env:LINGSHU_PYTHON) }
  $candidates.Add((Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'))
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -and $cmd.Source -notlike '*WindowsApps*') { $candidates.Add($cmd.Source) }
  foreach ($c in $candidates) {
    if (-not $c) { continue }
    if (-not (Test-Path -LiteralPath $c)) { continue }
    try {
      $ok = & $c -c "import sys; print(1 if sys.version_info>=(3,8) else 0)" 2>$null
      if ("$ok".Trim() -eq '1') { return $c }
    } catch { continue }
  }
  return $null
}

function Find-Node {
  $candidates = New-Object System.Collections.Generic.List[string]
  if ($env:LINGSHU_NODE) { $candidates.Add($env:LINGSHU_NODE) }
  $candidates.Add((Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'))
  $cmd = Get-Command node -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }
  foreach ($c in $candidates) {
    if ($c -and (Test-Path -LiteralPath $c)) { return $c }
  }
  return $null
}

function Test-PortBusy([int]$p) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $iar = $client.BeginConnect('127.0.0.1', $p, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(250)) { return $false }
    $client.EndConnect($iar)
    return $true
  } catch { return $false }
  finally { $client.Dispose() }
}

function Wait-Health([string]$baseUrl, [int]$timeoutSec = 20) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $null = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 1
      return $true
    } catch { Start-Sleep -Milliseconds 400 }
  }
  return $false
}

$repoRoot = Resolve-RepoRoot
$serverPy = Join-Path $repoRoot 'console\server.py'
if (-not (Test-Path -LiteralPath $serverPy)) {
  Write-Fail "找不到控制台服务：$serverPy" '请在仓库根运行本脚本（或显式 -Repo <仓库路径>）'
  exit 1
}

$py = Find-Python
if (-not $py) {
  Write-Fail '找不到 Python >= 3.8' @'
          安装 Python，或设置 LINGSHU_PYTHON / -Python；
          本机 DSH 自带运行时通常在 ~/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe
'@
  exit 1
}
Write-Step "Python: $py"

$serverArgs = @($serverPy, '--port', "$Port", '--interval', "$Interval")
if ($Registry) { $serverArgs += @('--registry', $Registry) }
if ($Profile) { $serverArgs += @('--default-profile', $Profile) }

if ($Check) {
  & $py @serverArgs --check
  exit $LASTEXITCODE
}

if (Test-PortBusy $Port) {
  Write-Fail "端口 $Port 已被占用" "换端口：.\scripts\console.ps1 -Port 3001（或先运行 console_stop.ps1）"
  exit 1
}

$dataDir = Join-Path $repoRoot '.console-data'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$serverLog = Join-Path $dataDir 'console.out.log'
$serverErr = Join-Path $dataDir 'console.err.log'
$pidFile = Join-Path $dataDir 'console.pid'
$base = "http://127.0.0.1:$Port"
$url = "$base/"

$feedProc = $null
if ($Demo) {
  $node = Find-Node
  if ($node) {
    $feed = Join-Path $repoRoot 'tools\console_demo_feed.mjs'
    $feedLog = Join-Path $dataDir 'demo-feed.log'
    $feedArgs = @($feed, '--profile', 'desktop-demo', '--turns', "$DemoTurns",
                  '--interval', "$DemoIntervalMs", '--reset')
    Write-Step "启动演示流量（合成数据，插件真代码 + mock 宿主）：desktop-demo · $DemoTurns 轮"
    $feedProc = Start-Process -FilePath $node -ArgumentList $feedArgs -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $feedLog -RedirectStandardError (Join-Path $dataDir 'demo-feed.err.log')
    Start-Sleep -Milliseconds 600
  } else {
    Write-Host '[WARN] 找不到 Node，-Demo 跳过演示流量（控制台仍可用）' -ForegroundColor Yellow
  }
}

$env:PYTHONIOENCODING = 'utf-8'
Write-Step "启动控制台服务（只读，独立进程）…"
$serverProc = Start-Process -FilePath $py -ArgumentList $serverArgs -PassThru -WindowStyle Hidden `
  -RedirectStandardOutput $serverLog -RedirectStandardError $serverErr
$serverProc.Id | Out-File -Encoding ascii -FilePath $pidFile

$healthy = Wait-Health -baseUrl $base -timeoutSec 20
if (-not $healthy) {
  Write-Fail "控制台没有在 20s 内就绪（端口 $Port）"
  if (Test-Path $serverErr) { Get-Content $serverErr -Tail 12 | ForEach-Object { Write-Host "  $_" } }
  try { Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue } catch { }
  if ($feedProc) { try { Stop-Process -Id $feedProc.Id -Force -ErrorAction SilentlyContinue } catch { } }
  exit 1
}

try {
  $profiles = Invoke-RestMethod -Uri "$base/api/profiles" -TimeoutSec 3
  Write-Host ''
  Write-Host 'LINGSHU 控制台已就绪（只读 · 不展示对话原文）' -ForegroundColor Cyan
  Write-Host "  $url"
  Write-Host "  profile 列表："
  foreach ($p in $profiles.profiles) {
    $state = if ($p.available) { '可用' } else { '未接入(' + $p.reason + ')' }
    Write-Host ("    - {0,-14} {1,-8} {2,-8} {3}" -f $p.id, $p.kind, $p.mode, $state)
  }
  if ($Demo) { Write-Host '  演示流量在后台增长：看「记忆条目」与「相变环形图」即可（合成数据）' }
  Write-Host ''
  Write-Host '  停止：Ctrl+C，或另开窗口运行 .\scripts\console_stop.ps1'
  Write-Host '  desktop 接入（人工确认）：.\scripts\console_desktop_onboard.ps1（默认 dry-run）'
  Write-Host ''

  if (-not $NoOpen) {
    try { Start-Process $url | Out-Null } catch { Write-Host '[WARN] 未能自动打开浏览器，请手动访问上面的 URL' -ForegroundColor Yellow }
  }

  # 前台保活：不依赖进程句柄（沙箱/ACL 环境下 Wait-Process 与 HasExited 都可能不可靠），
  # 而是每 2s 打一次 /api/health；连续 3 次失败说明服务已停（或端口被换），脚本退出并清理。
  $failures = 0
  while ($true) {
    Start-Sleep -Seconds 2
    try {
      $null = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 2
      $failures = 0
    } catch {
      $failures += 1
      if ($failures -ge 3) { break }
    }
  }
} finally {
  Write-Host ''
  Write-Step '正在停止本脚本启动的进程…'
  if ($feedProc) { try { Stop-Process -Id $feedProc.Id -Force -ErrorAction SilentlyContinue } catch { } }
  try { Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue } catch { }
  if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
  Write-Step '已停止（没有写任何数据）'
}
