#Requires -Version 5.1
<#
.SYNOPSIS
  停止由 scripts/console.ps1 启动的控制台（读 .console-data/console.pid）。

.EXAMPLE
  .\scripts\console_stop.ps1
  .\scripts\console_stop.ps1 -Port 3001 -Force   # PID 文件丢失时按端口找进程
#>
param(
  [int]$Port = 3000,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repo '.console-data\console.pid'

if (Test-Path -LiteralPath $pidFile) {
  $raw = (Get-Content -LiteralPath $pidFile -Raw).Trim()
  if ($raw -match '^\d+$') {
    $procId = [int]$raw
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p) {
      Stop-Process -Id $procId -Force
      Write-Host "[console] 已停止 PID $procId"
      Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
      exit 0
    }
    Write-Host "[console] PID $procId 已不存在，清理 PID 文件"
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    exit 0
  }
}

# 没有 PID 文件：按端口找监听进程（默认只报告，-Force 才结束）
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $conn) {
  Write-Host "[console] 端口 $Port 没有监听进程，无需停止"
  exit 0
}
$owner = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
if (-not $Force) {
  Write-Host "[console] 端口 $Port 被 PID $($conn.OwningProcess) ($($owner.ProcessName)) 占用；确认是控制台后加 -Force 再执行"
  exit 1
}
Stop-Process -Id $conn.OwningProcess -Force
Write-Host "[console] 已停止 PID $($conn.OwningProcess)（端口 $Port）"
