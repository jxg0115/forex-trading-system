# 停止外汇交易系统（主服务 8002 及外部引擎 8091 若在）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File stop_system.ps1
# 原理：按监听端口定位进程并强制停止，然后验证端口已释放。

$ports = @(8002, 8091)   # 8002=主系统；8091=外部引擎（已停，若被误拉起一并停）
$stopped = @()
$warnings = @()

foreach ($port in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($pid in $pids) {
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            $stopped += "port ${port} pid ${pid} ($($proc.ProcessName))"
        }
    }
    Start-Sleep -Milliseconds 800
    $left = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($left) {
        $warnings += "port ${port} 仍被监听: $($left | Select-Object -ExpandProperty OwningProcess -Unique)"
    } else {
        Write-Output "port ${port} 已释放"
    }
}

if ($stopped.Count -gt 0) {
    Write-Output ("已停止: " + ($stopped -join "; "))
} else {
    Write-Output "未发现运行中的系统进程（8002/8091 均未监听）"
}
if ($warnings.Count -gt 0) {
    Write-Output ("警告: " + ($warnings -join "; "))
    Write-Output "端口未释放可能是进程未及时退出，稍等后重试，或检查是否有新进程被拉起"
}