@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_forex_ai.ps1" %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Startup failed. Check data\startup_err.log
pause
exit /b 1
