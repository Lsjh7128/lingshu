#Requires -Version 5.1
<#
.SYNOPSIS
  【人工执行】把 LINGSHU 以 observe（静默监控）模式接入日常 desktop profile。
  默认是 dry-run，只打印计划；加 -Apply 才真正改文件。回滚用 -Rollback。

.WHY
  desktop profile 是被 Electron 应用独占管理的 profile；历史事故（2026-10-25）：
  把未登记的实验插件塞进 desktop 后**整个会话无法对话**。所以本脚本：
    - 只做两件事：node_modules 下建一个 junction + 在 cordis.patch.yml 追加一条 insert；
    - 不碰 package.json / pnpm-lock / node_modules 里其它东西；
    - 改动前备份 cordis.patch.yml（带时间戳）；
    - 默认 dry-run；只有 H0 本人确认后加 -Apply。
  接入后插件以 mode=observe 运行：不注册 prompt 段、不注册 waterfall 钩子、不注册工具，
  只被动写 state/memory/audit（见 docs/25 §5）。它可以被一键回滚。

.EXAMPLE
  .\scripts\console_desktop_onboard.ps1                 # 只看计划（不动任何文件）
  .\scripts\console_desktop_onboard.ps1 -Apply          # 真正接入（需要重启 DSH 应用）
  .\scripts\console_desktop_onboard.ps1 -Rollback       # 回滚接入（保留历史数据）
#>
param(
  [switch]$Apply,
  [switch]$Rollback,
  [switch]$Force,
  [string]$ProfileDir = '',
  [string]$Repo = ''
)

$ErrorActionPreference = 'Stop'
$MarkerBegin = '# LINGSHU-OBSERVE-BEGIN'
$MarkerEnd = '# LINGSHU-OBSERVE-END'
$InsertBlock = @"
$MarkerBegin (managed by scripts/console_desktop_onboard.ps1; rollback: -Rollback)
- insert:
    - id: dsh-lingshu
      name: dsh-lingshu
      config:
        mode: observe
        profileTag: desktop
$MarkerEnd
"@

function Write-Section([string]$t) { Write-Host ''; Write-Host "── $t ──" -ForegroundColor Cyan }
function Write-Plan([string]$t) { Write-Host "  · $t" }
function Write-Warn([string]$t) { Write-Host "[WARN] $t" -ForegroundColor Yellow }

if ($Apply -and $Rollback) {
  Write-Host '[FAIL] -Apply 与 -Rollback 不能同时使用'
  exit 2
}

$repoRoot = if ($Repo) { (Resolve-Path -LiteralPath $Repo).Path } else { Split-Path -Parent $PSScriptRoot }
$pluginDir = Join-Path $repoRoot 'plugin\dsh-lingshu'
if (-not $ProfileDir) { $ProfileDir = Join-Path $env:USERPROFILE '.dsh\profiles\desktop' }

if (-not (Test-Path -LiteralPath $ProfileDir)) {
  Write-Host "[FAIL] 找不到 desktop profile：$ProfileDir"
  Write-Host '       本脚本只支持本机 DSH 的默认 desktop profile；如路径不同请用 -ProfileDir 指定。'
  exit 2
}
if (-not (Test-Path -LiteralPath (Join-Path $pluginDir 'package.json'))) {
  Write-Host "[FAIL] 找不到插件包：$pluginDir"
  exit 2
}

$patch = Join-Path $ProfileDir 'cordis.patch.yml'
$nmDir = Join-Path $ProfileDir 'node_modules'
$link = Join-Path $nmDir 'dsh-lingshu'
$patchExists = Test-Path -LiteralPath $patch
$patchText = if ($patchExists) { Get-Content -LiteralPath $patch -Raw -Encoding UTF8 } else { '' }
$hasMarker = $patchText -and $patchText.Contains($MarkerBegin)
$linkExists = Test-Path -LiteralPath $link
$linkTarget = $null
if ($linkExists) {
  try { $linkTarget = (Get-Item -LiteralPath $link -Force).Target } catch { $linkTarget = $null }
  if ($linkTarget -is [array]) { $linkTarget = $linkTarget[0] }
}

# 应用是否在运行：改 profile 配置需要重启才生效；运行中改文件不会立刻崩，但要求确认。
$appRunning = $false
try {
  $appRunning = [bool](Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and ($_.Path -like '*DeepSeek Harness*' -or $_.Path -like '*deepseek-harness*') } |
    Select-Object -First 1)
} catch { $appRunning = $false }

if ($Rollback) {
  Write-Section '回滚计划'
  Write-Plan "profile：$ProfileDir"
  Write-Plan "junction：$link（存在=$linkExists，指向=$linkTarget）"
  Write-Plan "补丁：$patch（含 LINGSHU 段=$hasMarker）"
  if (-not $hasMarker -and -not $linkExists) {
    Write-Host '  [OK] 没有发现接入痕迹，无需回滚（不做任何改动）'
    exit 0
  }
  if ($appRunning -and -not $Force) {
    Write-Host '[FAIL] 检测到 DSH 应用正在运行。请先完全退出应用，或加 -Force 强制回滚。'
    exit 2
  }
  if (-not $Apply) {
    Write-Host ''
    Write-Host '  dry-run：未做任何改动。确认后执行：.\scripts\console_desktop_onboard.ps1 -Rollback -Apply'
    exit 0
  }
  if ($patchExists) {
    $bak = "$patch.bak-lingshu-rollback-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $patch -Destination $bak -Force
    Write-Plan "已备份：$bak"
    $newText = [regex]::Replace($patchText, "(?ms)^$([regex]::Escape($MarkerBegin)).*?^$([regex]::Escape($MarkerEnd))\r?\n?", '')
    [IO.File]::WriteAllText($patch, $newText, (New-Object System.Text.UTF8Encoding($false)))
    Write-Plan '已移除 cordis.patch.yml 中的 LINGSHU insert 段'
  }
  if ($linkExists) {
    $isOurLink = $linkTarget -and ($linkTarget.TrimEnd('\') -ieq $pluginDir.TrimEnd('\'))
    if (-not $isOurLink) {
      Write-Host "[FAIL] $link 存在但不是指向 $pluginDir 的 junction；为避免误删，未删除。请人工确认。"
      exit 2
    }
    # 只删 junction 本身，不递归删目标目录（防误删仓库）
    (Get-Item -LiteralPath $link -Force).Delete()
    Write-Plan '已删除 node_modules\dsh-lingshu junction（没有删除仓库里的插件源码）'
  }
  Write-Host ''
  Write-Host '  [OK] 回滚完成。请重启 DSH 应用。'
  Write-Host '       插件数据仍保留在 %USERPROFILE%\.dsh\plugins\dsh-lingshu\profiles\desktop\（如需删除请人工处理）。'
  exit 0
}

Write-Section '接入计划（observe 静默监控）'
Write-Plan "profile：$ProfileDir"
Write-Plan "插件源：$pluginDir"
Write-Plan "junction：$link → $pluginDir"
Write-Plan "补丁：$patch（追加一条 insert，mode=observe / profileTag=desktop）"
Write-Plan '不修改：package.json、pnpm-lock.yaml、node_modules 内其它内容'
Write-Plan '数据目录：%USERPROFILE%\.dsh\plugins\dsh-lingshu\profiles\desktop\（运行后自动创建）'
if ($appRunning) { Write-Warn '检测到 DSH 应用正在运行；接入后必须重启应用才生效。' }

$conflicts = @()
if ($hasMarker) { $conflicts += 'cordis.patch.yml 已含 LINGSHU 段（可能已接入）' }
if ($linkExists -and (-not $linkTarget -or ($linkTarget.TrimEnd('\') -ine $pluginDir.TrimEnd('\')))) {
  $conflicts += "node_modules\dsh-lingshu 已存在且不是指向本仓库的 junction（当前：$linkTarget）"
}
if ($conflicts.Count -gt 0) {
  Write-Host ''
  foreach ($c in $conflicts) { Write-Warn $c }
  if (-not $Force) {
    Write-Host '[FAIL] 存在冲突，未做任何改动。确认无害后可加 -Force，或用 -Rollback 先清理。'
    exit 2
  }
  Write-Warn '-Force 已指定：继续（仍会先备份补丁）'
}

if (-not $Apply) {
  Write-Host ''
  Write-Host '  dry-run：未做任何改动。确认后执行：.\scripts\console_desktop_onboard.ps1 -Apply'
  Write-Host '  回滚命令：.\scripts\console_desktop_onboard.ps1 -Rollback -Apply'
  exit 0
}

if ($appRunning -and -not $Force) {
  Write-Host '[FAIL] 检测到 DSH 应用正在运行。请先完全退出应用再接入（或加 -Force 承担风险）。'
  exit 2
}

# 1) 补丁：先备份，再追加/修复
if ($patchExists) {
  $bak = "$patch.bak-lingshu-observe-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  Copy-Item -LiteralPath $patch -Destination $bak -Force
  Write-Plan "已备份：$bak"
}
if (-not $hasMarker) {
  if (-not $patchExists -or $patchText.Trim() -eq '' -or $patchText.Trim() -eq '[]') {
    $newText = $InsertBlock + "`r`n"
  } else {
    $newText = $patchText.TrimEnd() + "`r`n`r`n" + $InsertBlock + "`r`n"
  }
  [IO.File]::WriteAllText($patch, $newText, (New-Object System.Text.UTF8Encoding($false)))
  Write-Plan '已写入 cordis.patch.yml'
} else {
  Write-Plan 'cordis.patch.yml 已含 LINGSHU 段，跳过写入（幂等）'
}

# 2) junction
New-Item -ItemType Directory -Force -Path $nmDir | Out-Null
if (-not (Test-Path -LiteralPath $link)) {
  New-Item -ItemType Junction -Path $link -Target $pluginDir | Out-Null
  Write-Plan '已创建 node_modules\dsh-lingshu junction'
} elseif ($linkTarget -and ($linkTarget.TrimEnd('\') -ieq $pluginDir.TrimEnd('\'))) {
  Write-Plan 'junction 已存在且指向本仓库，跳过'
} else {
  Write-Host '[FAIL] junction 状态异常，请人工检查后重试'
  exit 2
}

Write-Host ''
Write-Host '  [OK] 接入完成。下一步：'
Write-Host '        1) 完全退出并重启 DSH 应用；'
Write-Host '        2) 在 desktop 里正常对话 1–2 轮；'
Write-Host '        3) 运行 .\scripts\console.ps1，下拉框选择 desktop，应能看到记忆/相变/STPA 数据；'
Write-Host '        4) 如需移除：.\scripts\console_desktop_onboard.ps1 -Rollback -Apply（先退出应用）。'
Write-Host '  安全边界：observe 模式不注册 prompt 段 / waterfall 钩子 / 工具，不改宿主行为。'
