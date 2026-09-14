@echo off
rem ============================================================
rem  yue2-V100  start  --  double-click this file
rem
rem  This .bat is deliberately ASCII-only. All Chinese messages
rem  live in scripts\launch.ps1, because cmd.exe's code page
rem  (936 or 65001) turns Chinese text in a .bat into garbage.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PS=pwsh"
where pwsh >nul 2>nul
if errorlevel 1 set "PS=powershell"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo ============================================================
  echo  Launcher exited with code %RC%.
  echo.
  echo  If you ran stop.bat in another window, this is expected:
  echo  the GUI was killed on purpose.
  echo  Otherwise, read the messages above.
  echo ============================================================
  pause
)
rem Propagate the exit code. %RC% has to be expanded on the SAME line as
rem endlocal, otherwise it is already gone by the time exit /b reads it.
endlocal & exit /b %RC%
