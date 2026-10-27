@echo off
rem LINGSHU 控制台 · 双击即用（转发到 console.ps1，参数原样传递）
rem 只读本地服务：默认 http://127.0.0.1:3000
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0console.ps1" %*
set EXITCODE=%errorlevel%
endlocal & exit /b %EXITCODE%
