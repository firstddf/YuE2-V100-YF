@echo off
rem ============================================================
rem  yue2-V100  stop  --  double-click this file
rem
rem  Stops the yue2_service.py background service (port 1414)
rem  and the Gradio GUI (port 7860), then waits for the GPU
rem  memory to be released.
rem
rem  ASCII-only on purpose: see start.bat for why.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PS=pwsh"
where pwsh >nul 2>nul
if errorlevel 1 set "PS=powershell"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -Stop %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo ============================================================
  echo  Stop failed ^(exit code %RC%^).
  echo ============================================================
  pause
)
rem Same-line expansion: see start.bat.
endlocal & exit /b %RC%
