# scripts/chaos_schedule.ps1 —— LINGSHU-Chaos-Night 计划任务管理（A5）
#
# 管理 Windows 计划任务 "LINGSHU-Chaos-Night"：每天 02:00 跑 scripts/chaos_night.ps1。
# 02:00–05:00 的 host 档开关由 chaos_night.ps1 自己按当前小时判断（任务本身不区分窗口）。
#
# 为什么用 /XML 而不是 /TR：/TR 里既有空格又有引号，schtasks 的引号转义在 PowerShell
# 里非常容易把路径打断；XML 用 <Command>/<Arguments> 分开表达，零转义歧义。
#
# 兼容 Windows PowerShell 5.1：所有语句单行，不用 PS7 的行尾运算符续行。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\chaos_schedule.ps1            # 注册（默认）
#   powershell ... -Status          # 查询（不存在也返回 0，方便夜间脚本/CI 探测）
#   powershell ... -Remove          # 删除并验证
#   powershell ... -Register        # 显式注册
#   powershell ... -Status --status # 兼容 --status/--remove 写法（$args 兜底）
#
# 退出码：0 = 操作成功（或 Status 查询完成）；1 = 注册/查询/删除失败。

param(
    [switch]$Status,
    [switch]$Remove,
    [switch]$Register,
    [string]$At = "02:00"
)

# 兼容 `--status` / `--remove` / `--register` 这类 GNU 风格调用
foreach ($a in $args) {
    if (-not $a) { continue }
    switch -Regex ([string]$a) {
        '^--?status$' { $Status = $true }
        '^--?remove$' { $Remove = $true }
        '^--?register$' { $Register = $true }
    }
}

$ErrorActionPreference = "Continue"
$taskName = "LINGSHU-Chaos-Night"
$repo = Split-Path -Parent $PSScriptRoot
$night = Join-Path $repo "scripts\chaos_night.ps1"
$powershellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path $powershellExe)) { $powershellExe = "powershell.exe" }

function Query-TaskVerbose() {
    $out = & schtasks.exe /Query /TN $taskName /V /FO LIST 2>&1
    $code = $LASTEXITCODE
    $text = ($out | Out-String).TrimEnd()
    return @{ code = $code; text = $text }
}

function Show-TaskSummary([string]$queryText) {
    $map = @{}
    foreach ($line in ($queryText -split "`r?`n")) {
        $idx = $line.IndexOf(":")
        if ($idx -gt 0) {
            $k = $line.Substring(0, $idx).Trim()
            $v = $line.Substring($idx + 1).Trim()
            if ($v -ne "" -and -not $map.ContainsKey($k)) { $map[$k] = $v }
        }
    }
    $keys = @("TaskName", "Status", "Next Run Time", "Last Run Time", "Last Result", "Schedule Type", "Start Time", "Task To Run")
    foreach ($k in $keys) {
        if ($map.ContainsKey($k)) { Write-Host ("  {0,-14}: {1}" -f $k, $map[$k]) }
    }
}

function XmlEsc([string]$s) {
    return $s.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;").Replace('"', "&quot;")
}

Write-Host ("任务名 : " + $taskName)
Write-Host ("脚本   : " + $night)

# ── 删除 ────────────────────────────────────────────────
if ($Remove) {
    $q = Query-TaskVerbose
    if ($q.code -ne 0) {
        Write-Host "任务当前不存在，无需删除（退出码 0）"
        exit 0
    }
    & schtasks.exe /Delete /TN $taskName /F 2>&1 | ForEach-Object { Write-Host ("  " + [string]$_) }
    $q2 = Query-TaskVerbose
    if ($q2.code -ne 0) {
        Write-Host "OK 已删除并查询确认：任务不存在"
        exit 0
    }
    Write-Host "ERROR 删除后仍能查到任务，请人工检查" -ForegroundColor Red
    exit 1
}

# ── 查询（-Status）────────────────────────────────────────
if ($Status -and -not $Register) {
    $q = Query-TaskVerbose
    if ($q.code -eq 0) {
        Write-Host "`n== schtasks /Query /TN $taskName /V /FO LIST ==" -ForegroundColor Green
        Write-Host $q.text
        Write-Host "`n已注册摘要：" -ForegroundColor Green
        Show-TaskSummary $q.text
        exit 0
    }
    Write-Host ("任务未注册（schtasks 退出码 " + $q.code + "）")
    Write-Host $q.text
    Write-Host ("提示：不加参数运行本脚本即可注册每日 " + $At + " 任务。")
    exit 0
}

# ── 注册 ────────────────────────────────────────────────
if (-not (Test-Path $night)) {
    Write-Host ("ERROR 找不到夜间脚本：" + $night) -ForegroundColor Red
    exit 1
}

$startBoundary = (Get-Date).ToString("yyyy-MM-dd") + "T" + $At + ":00"
$desc = "LINGSHU 混沌工程夜间任务：engine 档每日必跑；02:00-05:00 窗口内追加 host 档（模型调用上限 3 次）。数据目录隔离在 repo\.chaos-data。"
$xml = '<?xml version="1.0" encoding="UTF-16"?>' + "`r`n"
$xml += '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">' + "`r`n"
$xml += '  <RegistrationInfo>' + "`r`n"
$xml += ('    <Description>' + (XmlEsc $desc) + '</Description>') + "`r`n"
$xml += '  </RegistrationInfo>' + "`r`n"
$xml += '  <Triggers>' + "`r`n"
$xml += '    <CalendarTrigger>' + "`r`n"
$xml += ('      <StartBoundary>' + $startBoundary + '</StartBoundary>') + "`r`n"
$xml += '      <Enabled>true</Enabled>' + "`r`n"
$xml += '      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>' + "`r`n"
$xml += '    </CalendarTrigger>' + "`r`n"
$xml += '  </Triggers>' + "`r`n"
$xml += '  <Principals>' + "`r`n"
$xml += '    <Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>' + "`r`n"
$xml += '  </Principals>' + "`r`n"
$xml += '  <Settings>' + "`r`n"
$xml += '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>' + "`r`n"
$xml += '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>' + "`r`n"
$xml += '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>' + "`r`n"
$xml += '    <StartWhenAvailable>true</StartWhenAvailable>' + "`r`n"
$xml += '    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>' + "`r`n"
$xml += '    <Enabled>true</Enabled>' + "`r`n"
$xml += '  </Settings>' + "`r`n"
$xml += '  <Actions Context="Author">' + "`r`n"
$xml += '    <Exec>' + "`r`n"
$xml += ('      <Command>' + (XmlEsc $powershellExe) + '</Command>') + "`r`n"
$xml += ('      <Arguments>-NoProfile -ExecutionPolicy Bypass -File "' + (XmlEsc $night) + '"</Arguments>') + "`r`n"
$xml += '    </Exec>' + "`r`n"
$xml += '  </Actions>' + "`r`n"
$xml += '</Task>' + "`r`n"

$xmlPath = Join-Path ([System.IO.Path]::GetTempPath()) ("lingshu-chaos-task-" + (Get-Date -Format "yyyyMMddHHmmss") + ".xml")
try {
    $enc = [System.Text.UnicodeEncoding]::new($false, $true)
    [System.IO.File]::WriteAllText($xmlPath, $xml, $enc)
    Write-Host ("`n注册：每天 " + $At + " | 运行 " + $night)
    $out = & schtasks.exe /Create /TN $taskName /XML $xmlPath /F 2>&1
    $createCode = $LASTEXITCODE
    foreach ($line in $out) { Write-Host ("  " + [string]$line) }
    if ($createCode -ne 0) {
        Write-Host ("ERROR 注册失败（schtasks 退出码 " + $createCode + "）。环境拒绝注册时请如实记录，不要伪造成功。") -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host ("ERROR 注册异常：" + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $xmlPath
}

# 注册后必须查询验证
$q = Query-TaskVerbose
if ($q.code -eq 0) {
    Write-Host "`n== 注册验证：schtasks /Query /TN $taskName /V /FO LIST ==" -ForegroundColor Green
    Write-Host $q.text
    Write-Host "`nOK 已注册并查询确认：" -ForegroundColor Green
    Show-TaskSummary $q.text
    exit 0
}
Write-Host "ERROR 注册命令返回 0 但查询不到任务，请人工检查" -ForegroundColor Red
exit 1
