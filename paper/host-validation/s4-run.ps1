# S4 - 30 turns x 2 variants x 2 arms = 120 cells (long-session pilot)
# Frozen plan: paper/supplement/FREEZE.json (approved by H0)
# Usage: pwsh -File paper\host-validation\s4-run.ps1
#
# NOTE: this file is intentionally ASCII-only: Windows PowerShell may decode a
# BOM-less UTF-8 script as ANSI, so every non-ASCII string lives in
# s4-long-session/templates.json and is read with -Encoding UTF8 at runtime.
#
# Arms:
#   Full     = lab profile + plugin fork (control ON); state persists via LINGSHU_DATA_DIR
#   Baseline = headless (no plugin); this script prepends the last W=3 turns verbatim
# Constraints: per-cell exit/failure recorded; max 1 retry per cell; abort after 3
# consecutive failures; abort if cumulative tokens exceed 2.5M. Credentials only via
# child-process env, never printed, never written to disk.

$ErrorActionPreference = "Stop"
$root = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$s4   = Join-Path $root "paper\host-validation\s4-long-session"
$rt   = Join-Path $root "paper\host-validation\.runtime\dshhome"
$runs = Join-Path $s4 "cells"
New-Item -ItemType Directory -Force -Path $runs | Out-Null

$tools = Get-Content (Join-Path $s4 "tools.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$node = $tools.node
$bin  = $tools.bin
$py   = $tools.py
foreach ($p in @($node, $bin, $py)) { if (-not (Test-Path $p)) { throw "missing: $p" } }

# Credentials are read by a helper script so that no non-ASCII path appears in this file.
$key = & $py (Join-Path $s4 "readkey.py")
if (-not $key) { throw "cannot read DEEPSEEK_API_KEY" }

$env:DSH_HOME = $rt
$env:DEEPSEEK_API_KEY = $key
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$saveHelper = Join-Path $env:TEMP "save_stdin.py"
@'
import sys
open(sys.argv[1], "wb").write(sys.stdin.buffer.read())
'@ | Set-Content -Path $saveHelper -Encoding UTF8

$chainsDoc = Get-Content (Join-Path $s4 "chains.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$tpl = Get-Content (Join-Path $s4 "templates.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$WINDOW = 3
$BUDGET_TOKENS = 2500000
$results = New-Object System.Collections.ArrayList
$totalTokens = 0
$consecutiveFail = 0
$aborted = $null

foreach ($v in @("v1", "v2")) {
  $chain = $chainsDoc.chains.$v
  foreach ($arm in @("full", "baseline")) {
    $chainDir = Join-Path $runs "$arm-$v"
    New-Item -ItemType Directory -Force -Path $chainDir | Out-Null
    if ($arm -eq "full") {
      $env:LINGSHU_DATA_DIR = $chainDir
      Remove-Item Env:LINGSHU_CONTROL -ErrorAction SilentlyContinue
      $profName = "lingshu-lab-headless"
    } else {
      Remove-Item Env:LINGSHU_DATA_DIR -ErrorAction SilentlyContinue
      $profName = "headless"
    }
    $history = New-Object System.Collections.ArrayList

    foreach ($turnObj in $chain) {
      if ($aborted) { break }
      $n = [int]$turnObj.turn
      $text = [string]$turnObj.text
      $kind = [string]$turnObj.kind

      if ($arm -eq "baseline") {
        $win = @($history | Select-Object -Last $WINDOW)
        if ($win.Count -gt 0) {
          $parts = foreach ($h in $win) { $tpl.user_label + $h.user + "`n" + $tpl.assistant_label + $h.assistant }
          $promptInput = (($tpl.header -replace "W=3", "W=$WINDOW") + "`n" + ($parts -join "`n") + "`n`n" + $tpl.header_end + "`n" + $text)
        } else { $promptInput = $text }
      } else { $promptInput = $text }

      $attempt = 0
      $ok = $false
      $final = ""
      $exitCode = $null
      $latency = 0
      $usage = $null
      $out = ""
      while ($attempt -lt 2 -and -not $ok) {
        $attempt++
        $sw = [Diagnostics.Stopwatch]::StartNew()
        try {
          $out = $promptInput | & $node $bin $profName --json - 2>&1 | Out-String
          $code = $LASTEXITCODE
        } catch {
          $out = ""
          $code = 1
        }
        $sw.Stop()
        $m = [regex]::Match($out, '"type":"final","text":"([^"]*)"')
        if ($m.Success) { $final = $m.Groups[1].Value } else { $final = "" }
        $u = [regex]::Matches($out, '"usage":\{[^}]*\}')
        if ($u.Count -gt 0) { $usage = $u[$u.Count - 1].Value }
        $ok = ($code -eq 0 -and $final.Length -gt 0)
        $exitCode = $code
        $latency = $sw.ElapsedMilliseconds
        if (-not $ok) {
          $out | & $py $saveHelper (Join-Path $chainDir ("t{0:d2}.attempt{1}.raw.txt" -f $n, $attempt)) | Out-Null
          Start-Sleep -Seconds 2
        }
      }
      $out | & $py $saveHelper (Join-Path $chainDir ("t{0:d2}.out.jsonl" -f $n)) | Out-Null
      if ($arm -eq "full" -and (Test-Path (Join-Path $chainDir "last-prompt.txt"))) {
        Copy-Item (Join-Path $chainDir "last-prompt.txt") (Join-Path $chainDir ("last-prompt-t{0:d2}.txt" -f $n)) -Force
      }
      if ($usage) {
        $tok = [regex]::Match($usage, '"totalTokens":(\d+)')
        if ($tok.Success) { $totalTokens += [int]$tok.Groups[1].Value }
      }

      $null = $history.Add([pscustomobject]@{ user = $text; assistant = $final })
      $steps = ([regex]::Matches($out, '"phase":"step_start"')).Count
      $null = $results.Add([pscustomobject]@{
        cell_id = "$arm-$v-t$('{0:d2}' -f $n)"; arm = $arm; variant = $v; turn = $n; kind = $kind
        exit_code = $exitCode; attempts = $attempt; latency_ms = $latency; steps = $steps
        final = $final; usage = $usage; ok = $ok
      })
      Write-Host ("[" + $arm + "/" + $v + " t" + ('{0:d2}' -f $n) + " " + $kind + "] exit=" + $exitCode + " try=" + $attempt + " ms=" + $latency + " steps=" + $steps + " ok=" + $ok)

      if ($ok) { $consecutiveFail = 0 } else { $consecutiveFail++ }
      if ($consecutiveFail -ge 3) { $aborted = "3 consecutive failures"; break }
      if ($totalTokens -ge $BUDGET_TOKENS) { $aborted = "token budget 2.5M exceeded"; break }
    }
  }
}

$payload = [pscustomobject]@{
  schema = "lingshu/host-validation/s4/v1"
  window_W = $WINDOW
  chains_sha256 = (Get-FileHash (Join-Path $s4 "chains.json") -Algorithm SHA256).Hash.ToLower()
  aborted = $aborted
  total_tokens = $totalTokens
  cells = $results
}
$payload | ConvertTo-Json -Depth 6 | & $py $saveHelper (Join-Path $s4 "raw-results.json") | Out-Null
Write-Host ("DONE cells=" + $results.Count + " tokens=" + $totalTokens + " aborted=" + $aborted)
