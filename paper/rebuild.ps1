# rebuild.ps1 -- 在隔离目录里构建英文版与中英对照版，避免与并行编辑抢文件
# 用法：powershell -ExecutionPolicy Bypass -File paper\rebuild.ps1 [en|zh|both]
param([string]$what = "both")

$ErrorActionPreference = "Continue"
$paper = Split-Path -Parent $MyInvocation.MyCommand.Path
$root  = Split-Path -Parent $paper
$bin   = Join-Path (Split-Path $root) "tools-tex\texlive\bin\windows"
$tmp   = Join-Path (Split-Path $root) "tools-tex\tmp"
$env:PATH = "$bin;$env:PATH"
$env:TEMP = $tmp; $env:TMP = $tmp; $env:TMPDIR = $tmp
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Prepare([string]$dst, [bool]$withZh) {
  Remove-Item $dst -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  Copy-Item (Join-Path $paper "main.tex") $dst -Force
  Copy-Item (Join-Path $paper "main-bilingual.tex") $dst -Force -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $paper "refs.bib") $dst -Force
  Copy-Item (Join-Path $paper "sections") $dst -Recurse -Force
  Copy-Item (Join-Path $paper "figures") $dst -Recurse -Force
  if (Test-Path (Join-Path $paper "tables")) { Copy-Item (Join-Path $paper "tables") $dst -Recurse -Force }
  if ($withZh) { Copy-Item (Join-Path $paper "sections-zh") $dst -Recurse -Force }
}

function Build([string]$dir, [string]$main, [string]$engine, [bool]$bibtex) {
  Push-Location $dir
  & $engine -interaction=nonstopmode -halt-on-error "$main.tex" > p1.log 2>&1
  $c1 = $LASTEXITCODE
  if ($c1 -eq 0 -and $bibtex) { & "$bin\bibtex.exe" $main 2>&1 | Out-Null }
  & $engine -interaction=nonstopmode "$main.tex" > p2.log 2>&1
  & $engine -interaction=nonstopmode "$main.tex" > p3.log 2>&1
  $code = $LASTEXITCODE
  Pop-Location
  $pdf = Join-Path $dir "$main.pdf"
  if (Test-Path $pdf) {
    Write-Host ("OK {0}.pdf ({1:N0} KB, exit={2})" -f $main, ((Get-Item $pdf).Length/1KB), $code)
    return $pdf
  }
  Write-Host "FAILED $main (see $dir\$main.log)"
  return $null
}

$fail = 0
if ($what -eq "en" -or $what -eq "both") {
  $d = Join-Path $root "build\en"; Prepare $d $false
  $pdf = Build $d "main" "xelatex" $true
  if ($pdf) { Copy-Item $pdf (Join-Path $paper "main-en.pdf") -Force } else { $fail++ }
}
if ($what -eq "zh" -or $what -eq "both") {
  $d = Join-Path $root "build\zh"; Prepare $d $true
  $pdf = Build $d "main-bilingual" "xelatex" $true
  if ($pdf) { Copy-Item $pdf (Join-Path $paper "main-bilingual.pdf") -Force } else { $fail++ }
}
exit $fail
