@echo off
setlocal
rem Double-click entry; all setup logic lives in the PowerShell/WSL helpers.
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0tools\windows-setup.ps1" -Graphical
set "result=%errorlevel%"
echo.
if not "%result%"=="0" echo Setup did not complete. Read the message above, then run this file again.
pause
exit /b %result%
