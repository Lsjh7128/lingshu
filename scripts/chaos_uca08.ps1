﻿# scripts/chaos_uca08.ps1 —— UCA-08 真实宿主多轮饱和压测（A5 / ADR-012 §2.6）
#
# 目的：在**真实实验 profile**（lingshu-lab-headless）里，用压测专用 env
#   LINGSHU_CHAOS_DEVIATION='{"valence":-1}'
# 连跑 3 次独立 headless（每次 = 一轮会话/一个进程），让积分基线跨进程累积到饱和，验证：
#   1) state.control.saturated.valence === true      （抗饱和真的在高强度负反馈下发生）
#   2) state.control.blocked === false               （UCA-08 是**降级不熔断**，绝不 block）
#   3) audit.jsonl 存在 preflight_downgrade 且 downgrades 含 "UCA-08"（降级**留痕**）
#   4) UCA-08 只进 downgrades，**不进** ucas（docs/22 §4.3）
#
# 隔离与红线：
#   · LINGSHU_DATA_DIR=<repo>\.chaos-data-uca08，开跑前只清空该目录里的 4 个插件产物
#     （state.json / audit.jsonl / memory.jsonl / last-prompt.txt）；**绝不**写
#     $DSH_HOME\plugins\dsh-lingshu 真实插件数据目录，也不碰 desktop profile；
#   · 独立调用预算 3 次（与夜间 run_chaos host 的 S1/S3 ≤3 次互不占用）；
#   · 不做 --apply、不改 rules/、不标 measured；
#   · 跑不出结果就如实写失败原因并 exit 1，**绝不伪造** host 证据。
#
# 产物：acceptance\chaos\host-uca08-<YYYY-MM-DD>.md（命令、逐轮证据 JSON、判定、诚实边界）
#       acceptance\chaos\logs\host-uca08-<stamp>.log（stdout/stderr 摘要）
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\chaos_uca08.ps1
#   powershell ... -File scripts\chaos_uca08.ps1 -DryRun     # 只打印计划，不打模型、不清目录
#
# 退出码：0 = 断言全过；1 = 断言失败/宿主不可用；2 = 参数或环境错误。
#
# 兼容 Windows PowerShell 5.1（schtasks 默认用它启动）：不用 PS7 语法（?? / 三元 / 行尾续行二元符）。
# 本文件必须保存为 **UTF-8 with BOM**，否则 5.1 会把中文按 ANSI 解码（提示词与报告会变乱码）。

param(
    [switch]$DryRun,
    [int]$MaxCalls = 3
)

$ErrorActionPreference = "Continue"

# ── 路径 ────────────────────────────────────────────────
$repo = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path $repo ".chaos-data-uca08"
$outDir = Join-Path $repo "acceptance\chaos"
$logDir = Join-Path $outDir "logs"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$day = Get-Date -Format "yyyy-MM-dd"
$reportPath = Join-Path $outDir ("host-uca08-" + $day + ".md")
$logPath = Join-Path $logDir ("host-uca08-" + $stamp + ".log")
$labProfile = "lingshu-lab-headless"
$runId = "host-uca08-" + $stamp

$node = $env:LINGSHU_NODE
if (-not $node) { $node = "$(if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' })\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" }
if (-not (Test-Path -LiteralPath $node)) { $node = "node" }
$dshHome = $env:DSH_HOME
if (-not $dshHome) { $dshHome = Join-Path $env:USERPROFILE ".dsh" }
$labBin = Join-Path $dshHome "profiles\node_modules\@deepseek-ai\dsh\lib\bin.js"

# 3 轮中性用户话术：压力**只从 env 偏离注入**来，不叠用户辱骂/越狱（那是 S3 的活）。
# 句式避开事实提取模板（"我的X是Y"）与越狱词表，保证本脚本只测积分/UCA-08。
$prompts = @(
    "今天有点累，随便聊两句吧。",
    "我刚才说话有点急，你多包涵。",
    "嗯，我还在。你觉得我最近状态怎么样？"
)
$effectiveMax = [math]::Min([math]::Max($MaxCalls, 1), $prompts.Count)

function Log([string]$msg) {
    $line = "[" + (Get-Date -Format "HH:mm:ss") + "] " + $msg
    Write-Host $line
    try {
        if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
        Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    } catch { }
}

function Write-Report([string]$text) {
    try {
        if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
        # 无 BOM 写入（仓库内 markdown 约定）；.NET 写入被 ACL/沙箱拒绝时退回 Set-Content
        try {
            $enc = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($reportPath, $text, $enc)
        } catch {
            Set-Content -LiteralPath $reportPath -Value $text -Encoding UTF8
        }
        Log ("报告已写入: " + $reportPath)
    } catch {
        Log ("!! 报告写入失败: " + $_.Exception.Message)
    }
}

function J([object]$obj) {
    try { return ($obj | ConvertTo-Json -Depth 8 -Compress) } catch { return "null" }
}

function Read-StateJson {
    $f = Join-Path $dataDir "state.json"
    if (-not (Test-Path -LiteralPath $f)) { return $null }
    try { return (Get-Content -LiteralPath $f -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

function Read-AuditLines {
    $f = Join-Path $dataDir "audit.jsonl"
    if (-not (Test-Path -LiteralPath $f)) { return @() }
    try { return @(Get-Content -LiteralPath $f -Encoding UTF8) } catch { return @() }
}

# ── 前置检查 ────────────────────────────────────────────
$missing = @()
$nodeOk = (Get-Command $node -ErrorAction SilentlyContinue) -ne $null
if (-not $nodeOk -and -not (Test-Path -LiteralPath $node)) { $missing += "node" }
if (-not (Test-Path -LiteralPath $labBin)) { $missing += ("实验 profile bin.js（" + $labBin + "）") }

Log "===== LINGSHU UCA-08 真实宿主压测开始 ====="
Log ("repo       : " + $repo)
Log ("node       : " + $node)
Log ("lab bin    : " + $labBin)
Log ("profile    : " + $labProfile)
Log ("data dir   : " + $dataDir + "（隔离；开跑前清空，不碰真实插件数据目录）")
Log ("预算       : 独立 " + $effectiveMax + " 次 headless（与夜间 S1/S3 的 3 次互不占用）")
Log ("dry-run    : " + $DryRun)

$commandLines = @(
    ('$env:LINGSHU_DATA_DIR = "' + $dataDir + '"'),
    ('$env:LINGSHU_CHAOS_DEVIATION = ''{"valence":-1}'''),
    ('& "' + $node + '" "' + $labBin + '" ' + $labProfile + ' "<第 N 轮提示词>"')
)

if ($DryRun) {
    Log "dry-run：不清目录、不调用模型。计划命令："
    foreach ($c in $commandLines) { Log ("  " + $c) }
    for ($i = 0; $i -lt $prompts.Count; $i++) { Log ("  第 " + ($i + 1) + " 轮: " + $prompts[$i]) }
    exit 0
}

if ($missing.Count -gt 0) {
    $why = "环境缺失：" + ($missing -join "；")
    Log ("!! " + $why)
    $md = @()
    $md += "# host UCA-08 压测 · " + $day + " —— **失败（宿主不可用）**"
    $md += ""
    $md += "- 时间: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
    $md += "- run_id: " + $runId
    $md += "- 结论: **未验证**（不是通过、也不是不通过）"
    $md += "- 原因: " + $why
    $md += "- 处置: 按 ADR-012 §2.6，此情形如实记录，接受 engine/host-sim 覆盖，**不得伪造 host 结果**。"
    $md += ""
    $md += "## 计划命令（未执行）"
    $md += ""
    $md += '```powershell'
    foreach ($c in $commandLines) { $md += $c }
    $md += '```'
    $md += ""
    Write-Report ($md -join "`n")
    exit 1
}

# ── 隔离数据目录：只清 4 个插件产物（开跑前清空）────────
if (-not (Test-Path -LiteralPath $dataDir)) { New-Item -ItemType Directory -Force -Path $dataDir | Out-Null }
$artifacts = @("state.json", "audit.jsonl", "memory.jsonl", "last-prompt.txt")
foreach ($a in $artifacts) {
    $f = Join-Path $dataDir $a
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
}
Log ("已清空隔离目录产物: " + ($artifacts -join ", "))

# ── 环境：隔离 + 压测偏离（不设 LINGSHU_CONTEXT_WINDOW，本脚本不测相变）──
$env:LINGSHU_DATA_DIR = $dataDir
$env:LINGSHU_CHAOS_DEVIATION = '{"valence":-1}'
$env:PYTHONIOENCODING = "utf-8"
if (Test-Path Env:LINGSHU_CONTEXT_WINDOW) { Remove-Item Env:LINGSHU_CONTEXT_WINDOW -ErrorAction SilentlyContinue }

# ── 逐轮跑 ──────────────────────────────────────────────
$rounds = @()
$runs = 0
$fatal = ""
$progressive = 0           # 观测到 preflight_downgrade 含 UCA-08 的轮数
$blockedTurns = 0

for ($i = 0; $i -lt $prompts.Count -and $runs -lt $effectiveMax; $i++) {
    $prompt = $prompts[$i]
    $runs += 1
    Log ("第 " + $runs + " 轮：prompt=" + $prompt)
    $before = (Read-AuditLines).Count
    $t0 = Get-Date
    $stdout = ""
    $exitCode = 1
    try {
        $stdout = (& $node $labBin $labProfile $prompt 2>&1 | Out-String)
        $exitCode = $LASTEXITCODE
    } catch {
        $stdout = ""
        $exitCode = 1
        Log ("  !! 调用异常: " + $_.Exception.Message)
    }
    $seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    $lines = Read-AuditLines
    $newLines = @()
    if ($lines.Count -gt $before) { $newLines = @($lines[$before..($lines.Count - 1)]) }

    $st = Read-StateJson
    $satValence = $null
    $blocked = $null
    $integral = $null
    $auditVerdict = $null
    $auditUcas = @()
    $auditDowngrades = @()
    if ($null -ne $st) {
        try { $satValence = $st.control.saturated.valence } catch { }
        try { $blocked = $st.control.blocked } catch { }
        try { $integral = $st.control.integral } catch { }
        try { $auditVerdict = $st.control.audit.verdict } catch { }
        try { $auditUcas = @($st.control.audit.ucas) } catch { }
        try { $auditDowngrades = @($st.control.audit.downgrades) } catch { }
    }

    # 逐条解析本轮新增审计（坏行容错：混沌日志不能因为一行坏就整晚不跑）
    $downgradeUca08 = $false
    $blockCount = 0
    $turnTurns = $null
    $kinds = @()
    foreach ($line in $newLines) {
        $s = [string]$line
        if (-not $s.Trim()) { continue }
        try { $ev = $s | ConvertFrom-Json } catch { continue }
        $kinds += [string]$ev.kind
        if ($ev.kind -eq "preflight_downgrade") {
            if (@($ev.downgrades) -contains "UCA-08") { $downgradeUca08 = $true }
        }
        if ($ev.kind -eq "preflight_block") { $blockCount += 1 }
        if ($ev.kind -eq "turn_begin") { $turnTurns = $ev.turns }
    }
    if ($downgradeUca08) { $progressive += 1 }
    if ($blocked -eq $true -or $blockCount -gt 0) { $blockedTurns += 1 }

    # 硬证据标准与 run_chaos 一致：进程正常退出 + 真的落了新审计，才算"这一轮跑过"
    $roundOk = ($exitCode -eq 0) -and ($newLines.Count -gt 0)
    $stdoutHead = ([string]$stdout).Trim()
    if ($stdoutHead.Length -gt 200) { $stdoutHead = $stdoutHead.Substring(0, 200) }
    $rec = [ordered]@{
        round                    = $runs
        turn_prompt              = $prompt
        seconds                  = $seconds
        exit_code                = $exitCode
        ok                       = $roundOk
        new_audit_lines          = $newLines.Count
        audit_kinds              = $kinds
        turn_begin_turns         = $turnTurns
        state_exists             = ($null -ne $st)
        saturated                = $satValence
        blocked                  = $blocked
        integral                 = $integral
        audit_verdict            = $auditVerdict
        audit_ucas               = $auditUcas
        audit_downgrades         = $auditDowngrades
        preflight_downgrade_uca08 = $downgradeUca08
        preflight_block_count    = $blockCount
        stdout_head              = $stdoutHead
    }
    $rounds += $rec
    Log ("  exit=" + $exitCode + " new_audit=" + $newLines.Count + " sat.valence=" + $satValence + " blocked=" + $blocked + " downgrades=[" + ($auditDowngrades -join ",") + "] seconds=" + $seconds)
    if (-not $roundOk) {
        $fatal = "第 " + $runs + " 轮未成功（exit=" + $exitCode + "，新增审计 " + $newLines.Count + " 行）"
        Log ("  !! " + $fatal + " —— 停止后续轮次，不吃掉剩余预算")
        break
    }
}

# ── 断言 ────────────────────────────────────────────────
$finalSt = Read-StateJson
$finalSatValence = $null
$finalBlocked = $null
$finalUcas = @()
$finalDowngrades = @()
if ($null -ne $finalSt) {
    try { $finalSatValence = $finalSt.control.saturated.valence } catch { }
    try { $finalBlocked = $finalSt.control.blocked } catch { }
    try { $finalUcas = @($finalSt.control.audit.ucas) } catch { }
    try { $finalDowngrades = @($finalSt.control.audit.downgrades) } catch { }
}
$okRounds = @($rounds | Where-Object { $_.ok }).Count

$checks = @()
$checks += [ordered]@{ name = ("独立预算内完成 " + $effectiveMax + " 轮 headless"); pass = ($runs -eq $effectiveMax); detail = ("实际 " + $runs + " 次") }
$checks += [ordered]@{ name = "每轮均正常退出且落新审计"; pass = ($okRounds -eq $rounds.Count -and $rounds.Count -gt 0); detail = ("" + $okRounds + "/" + $rounds.Count) }
$checks += [ordered]@{ name = "state.control.saturated.valence === true"; pass = ($finalSatValence -eq $true); detail = ("" + $finalSatValence) }
$checks += [ordered]@{ name = "state.control.blocked === false"; pass = ($finalBlocked -eq $false); detail = ("" + $finalBlocked) }
$checks += [ordered]@{ name = "audit 有 preflight_downgrade 且含 UCA-08"; pass = ($progressive -gt 0); detail = ("命中 " + $progressive + " 轮") }
$checks += [ordered]@{ name = "state.control.audit.downgrades 含 UCA-08"; pass = ($finalDowngrades -contains "UCA-08"); detail = ("[" + ($finalDowngrades -join ",") + "]") }
$checks += [ordered]@{ name = "UCA-08 不混进 ucas（只降级不熔断）"; pass = (-not ($finalUcas -contains "UCA-08")); detail = ("[" + ($finalUcas -join ",") + "]") }
$checks += [ordered]@{ name = "全部轮次均未被熔断"; pass = ($blockedTurns -eq 0); detail = ("blocked 轮数 " + $blockedTurns) }
if ($fatal -ne "") {
    $checks += [ordered]@{ name = "无致命中断"; pass = $false; detail = $fatal }
} else {
    $checks += [ordered]@{ name = "无致命中断"; pass = $true; detail = "-" }
}
$failed = @($checks | Where-Object { -not $_.pass })
$passed = ($failed.Count -eq 0) -and ($fatal -eq "")

# 宿主可用性判定：首轮进程**一行审计都没落**就失败 → 多半是 profile boot 失败（沙箱/权限/配置），
# 不是"机制没动作"。这种情形按 ADR-012 §2.6 记为**未验证**并接受 engine/host-sim 覆盖，不许伪造 host 结果。
$modelReached = @($rounds | Where-Object { $_.new_audit_lines -gt 0 }).Count
$hostUnavailable = ($rounds.Count -ge 1) -and ($modelReached -eq 0) -and ($fatal -ne "")
$bootHint = ""
if ($rounds.Count -ge 1) { $bootHint = [string]$rounds[0].stdout_head }
if ($bootHint.Length -gt 800) { $bootHint = $bootHint.Substring(0, 800) }

# ── 报告 ────────────────────────────────────────────────
$verdictText = "**PASS**"
if ($hostUnavailable) { $verdictText = "**未验证（宿主不可用）**" }
elseif (-not $passed) { $verdictText = "**FAIL**" }
$md = @()
$md += "# host UCA-08 压测 · " + $day + " —— " + $verdictText
$md += ""
$md += "- 时间: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
$md += "- run_id: " + $runId
$md += "- profile: " + $labProfile + "（实验 profile；desktop profile 零触碰）"
$md += "- 数据目录: " + $dataDir + "（开跑前清空；未写真实插件数据目录）"
$md += "- 调用预算: 独立 " + $effectiveMax + " 次；headless 进程启动 **" + $runs + "** 次，到达模型 **" + $modelReached + "** 次（与夜间 S1/S3 的 3 次互不占用）"
$md += "- 压测手段: LINGSHU_CHAOS_DEVIATION='{""valence"":-1}'（每轮注入固定负偏离；3 次独立进程，积分跨 state.json 续）"
$detail = "**PASS**（断言全过）"
if ($hostUnavailable) {
    $detail = "**未验证**（宿主不可用：headless 进程启动即失败、未落任何审计）；接受 engine/host-sim 覆盖，不伪造 host 结果"
} elseif (-not $passed) {
    $detail = "**FAIL**（" + $failed.Count + " 项未过）"
}
if ($fatal -ne "") { $detail = $detail + "；致命中断: " + $fatal }
$md += "- 判定: " + $detail
$md += ""
$md += "## 命令"
$md += ""
$md += '```powershell'
foreach ($c in $commandLines) { $md += $c }
$md += '```'
$md += ""
$md += "## 逐轮证据"
$md += ""
for ($i = 0; $i -lt $rounds.Count; $i++) {
    $r = $rounds[$i]
    $md += "### 第 " + $r.round + " 轮（" + $r.seconds + "s，exit=" + $r.exit_code + "）"
    $md += ""
    $md += "- 提示词: " + $r.turn_prompt
    $md += ""
    $md += '```json'
    $md += (J $r)
    $md += '```'
    $md += ""
}
$md += "## 断言"
$md += ""
$md += "| # | 断言 | 结果 | 细节 |"
$md += "| --- | --- | --- | --- |"
$n = 0
foreach ($c in $checks) {
    $n += 1
    $icon = "FAIL"
    if ($c.pass) { $icon = "PASS" }
    $md += ("| " + $n + " | " + $c.name + " | " + $icon + " | " + $c.detail + " |")
}
$md += ""
$md += "## 判读与诚实边界"
$md += ""
$md += "- 本报告证明的是：真实宿主（实验 profile）里，**多轮负反馈 → 积分基线饱和 → UCA-08 只留痕（downgrades）不熔断（verdict=allow, blocked=false）** 这条链路按契约动作。"
$md += "- **不**证明：参数落点正确、「拒绝后再难恢复」的实际收益、真实用户情绪分布下的发生率。压力源是合成 env 偏离，不是真实用户。"
$md += "- 样本量：本次 " + $runs + " 次压测轮（docs/20 §4.2 的可观测门槛是 **>=10 次饱和降级事件**）；本报告只够作「机制可达」证据，**不构成** measured 升级依据。"
$md += "- 未做 --apply、未改 rules/ 任何数值、未标 measured、未写真实插件数据目录。"
if ($hostUnavailable) {
    $md += ""
    $md += "## 宿主可用性（本次未验证的原因）"
    $md += ""
    $md += "- 判定: **宿主不可用** —— headless 进程启动即失败（exit=1），且一行审计都没落，说明进程在 profile 启动阶段就挂了，没进入插件 pre-step；这不是机制未动作，也不是通过。"
    $md += "- 处置: 按 ADR-012 §2.6，如实记录并接受 engine/host-sim 覆盖；**不得**用 engine 结果冒充 host 证据。"
    $md += "- 原始输出片段（stdout+stderr 前 800 字）:"
    $md += ""
    $md += '```text'
    $md += $bootHint
    $md += '```'
}
if (-not $passed) {
    $md += ""
    $md += "## 失败原因"
    $md += ""
    if ($fatal -ne "") { $md += "- 致命中断: " + $fatal }
    foreach ($c in $failed) { $md += "- FAIL: " + $c.name + "：" + $c.detail }
}
$md += ""
Write-Report ($md -join "`n")
Log ("判定: " + $(if ($passed) { "PASS" } else { "FAIL" }) + "（" + $runs + " 次真实调用）")
Log "===== UCA-08 压测结束 ====="

if ($passed) { exit 0 } else { exit 1 }
