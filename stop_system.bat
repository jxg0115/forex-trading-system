@echo off
setlocal EnableExtensions
rem Stop forex system: main port 8002 + external engine 8091 (if running).
rem Pure CMD with %SystemRoot% inline absolute paths (PATH-independent). ASCII only.

for %%P in (8002 8091) do (
    for /f "tokens=5" %%K in ('%SystemRoot%\System32\netstat.exe -ano ^| %SystemRoot%\System32\findstr.exe ":%%P " ^| %SystemRoot%\System32\findstr.exe "LISTENING"') do (
        echo Stopping PID %%K on port %%P ...
        %SystemRoot%\System32\taskkill.exe /F /PID %%K >nul 2>&1
    )
)

%SystemRoot%\System32\ping.exe -n 3 127.0.0.1 >nul

for %%P in (8002 8091) do (
    %SystemRoot%\System32\netstat.exe -ano | %SystemRoot%\System32\findstr.exe ":%%P " | %SystemRoot%\System32\findstr.exe "LISTENING" >nul
    if errorlevel 1 (
        echo port %%P released
    ) else (
        echo WARN: port %%P still listening
    )
)
echo.
pause