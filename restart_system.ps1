$ErrorActionPreference = "SilentlyContinue"
$root = $PSScriptRoot

# 给旧后端一点时间返回响应，然后再关闭进程
Start-Sleep -Seconds 2
try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8002/api/matcher/stop" -TimeoutSec 3 | Out-Null } catch {}
try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/api/matcher/stop" -TimeoutSec 3 | Out-Null } catch {}

# 关闭本项目涉及的所有 Python / Vite 进程
$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match "python|node" -and (
    $_.CommandLine -like "*main.py*" -or
    $_.CommandLine -like "*vite*" -or
    $_.CommandLine -like "*spawn_main*"
  )
}
foreach ($p in $procs) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 3

# 只保留一个后端端口：8002
$env:PORT = "8002"
$env:DEBUG = "0"
Start-Process -FilePath (Join-Path $root ".venv\Scripts\python.exe") -ArgumentList "main.py" -WorkingDirectory $root -WindowStyle Hidden
