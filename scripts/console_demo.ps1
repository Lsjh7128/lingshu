#Requires -Version 5.1
<#
.SYNOPSIS
  一键演示：启动合成流量（插件真代码 + mock 宿主）+ 控制台，验收"记忆条目增长 / 相变环形图变色"。

.DESCRIPTION
  等价于：.\scripts\console.ps1 -Demo -Profile desktop-demo
  停止：Ctrl+C（等同 console.ps1 的清理路径）。
  注意：这是**合成数据**，用于演示控制台观测链路；不是真实 DSH 流量，不能当标定证据。

.EXAMPLE
  .\scripts\console_demo.ps1
  .\scripts\console_demo.ps1 -Turns 60 -IntervalMs 800 -Port 3001
#>
param(
  [int]$Port = 3000,
  [int]$Turns = 40,
  [int]$IntervalMs = 1200,
  [switch]$NoOpen,
  [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$splat = @{
  Demo           = $true
  DemoTurns      = $Turns
  DemoIntervalMs = $IntervalMs
  Port           = $Port
  Profile        = 'desktop-demo'
  NoOpen         = [bool]$NoOpen
}
if ($Python) { $splat['Python'] = $Python }
& (Join-Path $PSScriptRoot 'console.ps1') @splat
exit $LASTEXITCODE
