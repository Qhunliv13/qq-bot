@echo off
setlocal
rem Native complete runtime by default; legacy WSL entry remains available explicitly.
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0tools\windows-native.ps1"
set "result=%errorlevel%"
echo.
if not "%result%"=="0" echo Setup did not complete. Read the message above, then run this file again.
pause
exit /b %result%
