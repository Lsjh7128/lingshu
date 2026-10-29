@echo off
rem demo.bat - LINGSHU sandbox demo launcher for Windows
rem
rem   demo.bat                 : check Node -> create sandbox profile -> seed "A-Fu" memory -> chat
rem   demo.bat --clean         : delete only the throwaway sandbox profile and .\.lingshu-demo-data
rem   demo.bat --help          : all flags and the isolation contract
rem
rem This wrapper only locates Node and forwards to "node demo\bootstrap.mjs";
rem the real logic lives in Node so demo.sh and demo.bat cannot drift apart.
setlocal EnableExtensions
chcp 65001 >nul 2>nul
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul

set "NODE_EXE="
if defined LINGSHU_NODE if exist "%LINGSHU_NODE%" set "NODE_EXE=%LINGSHU_NODE%"

if not defined NODE_EXE (
  for /f "delims=" %%I in ('where node 2^>nul') do (
    if not defined NODE_EXE set "NODE_EXE=%%I"
  )
)

if not defined NODE_EXE if defined DSH_HOME (
  for /d %%D in ("%DSH_HOME%\dsh-runtimes\*") do (
    if not defined NODE_EXE if exist "%%D\dependencies\node\bin\node.exe" set "NODE_EXE=%%D\dependencies\node\bin\node.exe"
  )
)

if not defined NODE_EXE (
  for /d %%D in ("%USERPROFILE%\.dsh\dsh-runtimes\*") do (
    if not defined NODE_EXE if exist "%%D\dependencies\node\bin\node.exe" set "NODE_EXE=%%D\dependencies\node\bin\node.exe"
  )
)

if not defined NODE_EXE (
  echo [demo] Node not found ^(need ^>= 20.10^).
  echo        Install from https://nodejs.org/ , then re-run demo.bat
  echo        Already installed elsewhere? set LINGSHU_NODE=C:\path\to\node.exe
  popd >nul
  exit /b 2
)

"%NODE_EXE%" -e "const [m,n]=process.versions.node.split('.').map(Number);process.exit((m>20)||(m===20&&n>=10)?0:1)"
if errorlevel 1 (
  echo [demo] Node is too old ^(need ^>= 20.10^). Detected:
  "%NODE_EXE%" --version
  popd >nul
  exit /b 2
)

"%NODE_EXE%" "%SCRIPT_DIR%demo\bootstrap.mjs" %*
set "CODE=%ERRORLEVEL%"
popd >nul
exit /b %CODE%
