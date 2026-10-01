@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_job_edge.ps1" %*
set "result=%errorlevel%"
if not "%result%"=="0" echo Installation failed. Please read the message above.
pause
exit /b %result%
