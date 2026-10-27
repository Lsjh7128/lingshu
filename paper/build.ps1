# build.ps1 -- 编译论文（英文版 / 中英对照版）
# 用法：powershell -ExecutionPolicy Bypass -File paper\build.ps1 [en|zh|both]
param([string]$what = "both")

$ErrorActionPreference = "Continue"
$paper = Split-Path -Parent $MyInvocation.MyCommand.Path
$root  = Split-Path -Parent $paper
$bin   = Join-Path (Split-Path $root) "tools-tex\texlive\bin\windows"
$tmp   = Join-Path (Split-Path $root) "tools-tex\tmp"
if (-not (Test-Path (Join-Path $bin "pdflatex.exe"))) { Write-Host "TeX Live 未找到: $bin"; exit 2 }
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:PATH = "$bin;$env:PATH"
$env:TEMP = $tmp; $env:TMP = $tmp; $env:TMPDIR = $tmp
$env:PYTHONIOENCODING = "utf-8"

function Run-Tex([string]$engine, [string]$main, [int]$times = 2) {
  Push-Location $paper
  & $engine -interaction=nonstopmode -halt-on-error "$main.tex" 2>&1 | Out-Null
  $code = $LASTEXITCODE
  if ($code -ne 0) { Pop-Location; return $code }
  if (Test-Path "$main.aux") { & bibtex "$main" 2>&1 | Out-Null }
  for ($i = 0; $i -lt $times; $i++) { & $engine -interaction=nonstopmode -halt-on-error "$main.tex" 2>&1 | Out-Null; $code = $LASTEXITCODE }
  Pop-Location
  return $code
}

function Build-One([string]$main, [string]$engine) {
  Write-Host "=== building $main ($engine) ==="
  $code = Run-Tex $engine $main 2
  $pdf = Join-Path $paper "$main.pdf"
  $log = Join-Path $paper "$main.log"
  if (Test-Path $pdf) {
    $size = [math]::Round((Get-Item $pdf).Length / 1KB, 1)
    Write-Host "OK: $main.pdf ($size KB, exit=$code)"
    return 0
  }
  Write-Host "FAILED: $main (see $log)"
  if (Test-Path $log) { Get-Content $log -Tail 25 }
  return 1
}

$fail = 0
if ($what -eq "en" -or $what -eq "both") { $fail += Build-One "main" "pdflatex" }
if ($what -eq "zh" -or $what -eq "both") { $fail += Build-One "main-bilingual" "xelatex" }
exit $fail
