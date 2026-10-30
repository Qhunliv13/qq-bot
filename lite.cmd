@echo off
setlocal
rem Double-click defaults to the native web UI; no installation or elevation occurs.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0lite.ps1" %*
set "result=%errorlevel%"
echo.
if not "%result%"=="0" echo Lite did not complete. Read the message above, then try again.
rem Match install.cmd: retain the console after shutdown or failure for double-click users.
pause
exit /b %result%
