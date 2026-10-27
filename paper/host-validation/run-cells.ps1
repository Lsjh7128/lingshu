# lab profile 真实宿主验证 · 6 格主矩阵运行脚本（本会话实际使用版本）
#
# 用法：pwsh -File paper\host-validation\run-cells.ps1
#
# 前置条件（见 REPORT.md §3.1，一次性）：
#   1) $env:DSH_HOME -> .host-valid-data\dshhome（profile 文件 + 插件副本已就位）
#   2) 凭证：从 ~/.dsh/.credentials.yaml 读 DEEPSEEK_API_KEY，只注入子进程环境，不打印、不落盘
#   3) 数据目录：.host-valid-data\cells\full-<q>\{state.json(warm),memory.jsonl}
#
# 注意（踩过的坑，别改）：
#   - Start-Process -RedirectStandardInput 在沙箱下被拒（Access denied），必须用 PowerShell 管道
#   - prompt 78KB > Windows 命令行上限，必须走 stdin（headless 的 "-" 参数）
#   - 每次 pwsh 进程都要重设 DSH_HOME（环境变量不跨进程保留）
#   - Full 臂必须预置 warm 阶段（state.turns>=3），否则冷启动门控清空召回（matched=0）

$ErrorActionPreference = "Stop"
$root  = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$wh    = Join-Path $root ".host-valid-data\dshhome"
$runs  = Join-Path $root ".host-valid-data\runs"
$cells = Join-Path $root ".host-valid-data\cells"
New-Item -ItemType Directory -Force -Path $runs | Out-Null

$node = "$(if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' })\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
$bin  = "$(if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' })\profiles\node_modules\@deepseek-ai\dsh\lib\bin.js"
$py   = "$(if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' })\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path $node)) { throw "node 缺失: $node" }
if (-not (Test-Path $bin))  { throw "bin.js 缺失: $bin" }

# 凭证：只进环境变量，不打印
$key = & $py -c "import os,re;raw=open(os.path.expanduser('~/.dsh/.credentials.yaml'),encoding='utf-8').read();print(re.search(r'^  DEEPSEEK_API_KEY:\s*(\S+)\s*$',raw,re.M).group(1))"
if (-not $key) { throw "无法从 ~/.dsh/.credentials.yaml 读取 DEEPSEEK_API_KEY" }

$env:DSH_HOME = $wh
$env:DEEPSEEK_API_KEY = $key
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

$saveHelper = Join-Path $env:TEMP "save_stdin.py"
@'
import sys
open(sys.argv[1], "wb").write(sys.stdin.buffer.read())
'@ | Set-Content -Path $saveHelper -Encoding UTF8

$rows = @()
foreach ($arm in @("full", "baseline")) {
  foreach ($q in @("q1", "q2", "q3")) {
    if ($arm -eq "full") {
      $env:LINGSHU_DATA_DIR = Join-Path $cells "full-$q"
      $profile = "lingshu-lab-headless"
    } else {
      Remove-Item Env:LINGSHU_DATA_DIR -ErrorAction SilentlyContinue
      $profile = "headless"
    }
    $pf = Join-Path $root "paper\host-validation\prompt-100turn-$q.txt"
    $prompt = [IO.File]::ReadAllText($pf, [Text.UTF8Encoding]::new($false))

    $sw = [Diagnostics.Stopwatch]::StartNew()
    $out = $prompt | & $node $bin $profile --json - 2>&1 | Out-String
    $sw.Stop()
    $code = $LASTEXITCODE

    $outFile = Join-Path $runs "$arm-$q.jsonl"
    $out | & $py $saveHelper $outFile | Out-Null

    $m = [regex]::Match($out, '"type":"final","text":"([^"]*)"')
    $final = if ($m.Success) { $m.Groups[1].Value } else { "?" }
    $steps = ([regex]::Matches($out, '"phase":"step_start"')).Count
    $rows += [pscustomobject]@{
      arm = $arm; probe = $q; exit = $code; latency_ms = $sw.ElapsedMilliseconds
      steps = $steps; final = $final; has_beta = ($final -match "贝塔"); raw = $outFile
    }
    Write-Host ("[$arm/$q] exit=$code {0}ms steps=$steps final=$final" -f $sw.ElapsedMilliseconds)
  }
}

$summary = Join-Path $runs "summary.json"
$rows | ConvertTo-Json -Depth 4 | & $py $saveHelper $summary | Out-Null
Write-Host ""
$rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
Write-Host "summary -> $summary"
