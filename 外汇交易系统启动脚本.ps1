# 外汇交易系统启动脚本
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dataDir = Join-Path $root "data"
$venvDir = Join-Path $root ".venv"
$uiDir = Join-Path $root "ui_layer"
$distDir = Join-Path $uiDir "dist"
$runtimeDir = Join-Path $root "runtime"
$wheelsDir = Join-Path $runtimeDir "wheels"
$bundledInstaller = Join-Path $runtimeDir "python-3.12.10-amd64.exe"
$bundledPython = Join-Path $runtimeDir "python312\python.exe"
$portablePython = Join-Path $runtimeDir "python-embed\python.exe"
$backendUrl = "http://127.0.0.1:8002/"
$healthUrl = "http://127.0.0.1:8002/api/health"

if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
}

$logFile = Join-Path $dataDir "startup.log"
$stdoutFile = Join-Path $dataDir "startup_out.log"
$stderrFile = Join-Path $dataDir "startup_err.log"

if (-not (Test-Path (Join-Path $root ".env")) -and (Test-Path (Join-Path $root ".env.example"))) {
    Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination (Join-Path $root ".env") -Force
    Add-Content -LiteralPath $logFile -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " 未找到 .env，已从 .env.example 生成默认配置") -Encoding UTF8
}

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

function Test-Backend {
    try {
        $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
        return $resp.status -eq "ok"
    } catch {
        return $false
    }
}

function Find-Python {
    $candidates = @()
    if (Test-Path $portablePython) {
        $candidates += $portablePython
    }
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (Test-Path $venvPython) {
        $candidates += $venvPython
    }
    if (Test-Path $bundledPython) {
        $candidates += $bundledPython
    }
    foreach ($name in @("py", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $candidates += $cmd.Source
        }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path $candidate)) {
            continue
        }
        try {
            $version = & $candidate -c "import sys; print('.'.join(map(str, sys.version_info[:2])))" 2>$null
            if ($version -match "^\d+\.\d+$") {
                $parts = $version -split "\."
                $major = [int]$parts[0]
                $minor = [int]$parts[1]
                if (($major -gt 3) -or ($major -eq 3 -and $minor -ge 11)) {
                    return $candidate
                }
            }
        } catch {
            # 忽略无法执行的 Python 候选
        }
    }
    return $null
}

function Install-Python {
    Write-Log "未检测到 Python 3.11+，尝试自动安装..."
    if (Test-Path $bundledInstaller) {
        Write-Log "使用压缩包内置 Python 安装包..."
        $installDir = Join-Path $runtimeDir "python312"
        New-Item -ItemType Directory -Path $installDir -Force | Out-Null
        $proc = Start-Process -FilePath $bundledInstaller -ArgumentList "/quiet", "InstallAllUsers=0", "TargetDir=$installDir", "Include_pip=1", "Include_launcher=0", "PrependPath=0" -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            throw "内置 Python 安装失败，退出码 $($proc.ExitCode)"
        }
        if (Test-Path $bundledPython) {
            return $bundledPython
        }
    }
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Log "尝试通过 winget 安装 Python 3.12..."
        & winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            $found = Find-Python
            if ($found) {
                return $found
            }
        } else {
            Write-Log "winget 安装失败，改用官方安装包..."
        }
    }

    $installer = Join-Path $dataDir "python-3.12.10-amd64.exe"
    if (-not (Test-Path $installer)) {
        $mirrors = @(
            "https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe",
            "https://mirrors.nju.edu.cn/python/3.12.10/python-3.12.10-amd64.exe",
            "https://mirrors.ustc.edu.cn/python/3.12.10/python-3.12.10-amd64.exe",
            "https://mirrors.bfsu.edu.cn/python/3.12.10/python-3.12.10-amd64.exe",
            "https://mirrors.aliyun.com/python-release/windows/python-3.12.0-amd64.exe",
            "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
        )
        Write-Log "下载 Python 安装包（国内镜像优先）..."
        $downloaded = $false
        foreach ($mirror in $mirrors) {
            for ($attempt = 1; $attempt -le 2; $attempt++) {
                try {
                    Invoke-WebRequest -Uri $mirror -OutFile $installer -TimeoutSec 300 -UseBasicParsing
                    $downloaded = $true
                    break
                } catch {
                    Write-Log ("下载失败：{0}（第 {1} 次）" -f $mirror, $attempt)
                    if (Test-Path $installer) {
                        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
                    }
                    Start-Sleep -Seconds 2
                }
            }
            if ($downloaded) {
                break
            }
        }
        if (-not $downloaded) {
            throw "Python 安装包下载失败，请检查网络或防火墙后重试"
        }
    }
    Write-Log "静默安装 Python 3.12..."
    $proc = Start-Process -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1", "Include_launcher=1" -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "Python 官方安装包安装失败，退出码 $($proc.ExitCode)"
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $localPython) {
        return $localPython
    }
    return Find-Python
}

function Ensure-Venv {
    $python = Find-Python
    if (-not $python) {
        $python = Install-Python
    }
    if (-not $python) {
        throw "Python 3.11+ 不可用，请手动安装后重试"
    }
    if ($python -eq $portablePython) {
        Write-Log "使用压缩包内置便携 Python"
        return $python
    }
    if (-not (Test-Path $venvDir)) {
        Write-Log "创建虚拟环境 .venv ..."
        & $python -m venv $venvDir
        if ($LASTEXITCODE -ne 0) {
            throw "虚拟环境创建失败"
        }
    }
    return (Join-Path $venvDir "Scripts\python.exe")
}

function Install-PythonDeps {
    param([string]$Python)
    Write-Log "检查并安装 Python 依赖..."
    if (Test-Path $wheelsDir) {
        $wheelFiles = Get-ChildItem -LiteralPath $wheelsDir -Filter "*.whl" -File -ErrorAction SilentlyContinue
        if ($wheelFiles.Count -gt 0) {
            Write-Log "使用压缩包内置离线依赖包..."
            & $Python -m pip install -r (Join-Path $root "requirements.txt") --disable-pip-version-check --quiet --no-index --find-links $wheelsDir
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Python 依赖已离线安装完成"
                return
            }
            Write-Log "离线依赖安装失败，回退到在线镜像..."
        }
    }
    $mirrors = @(
        @{ Url = "https://pypi.tuna.tsinghua.edu.cn/simple"; Host = "pypi.tuna.tsinghua.edu.cn" },
        @{ Url = "https://mirrors.aliyun.com/pypi/simple/"; Host = "mirrors.aliyun.com" },
        @{ Url = "https://pypi.mirrors.ustc.edu.cn/simple"; Host = "pypi.mirrors.ustc.edu.cn" },
        @{ Url = "https://pypi.org/simple"; Host = "pypi.org" }
    )
    $installed = $false
    foreach ($mirror in $mirrors) {
        Write-Log ("使用 pip 镜像：{0}" -f $mirror.Url)
        & $Python -m pip install --upgrade pip --disable-pip-version-check --quiet -i $mirror.Url --trusted-host $mirror.Host --retries 5 --timeout 60
        if ($LASTEXITCODE -ne 0) {
            Write-Log "pip 升级失败，切换下一个镜像..."
            continue
        }
        & $Python -m pip install -r (Join-Path $root "requirements.txt") --disable-pip-version-check --quiet -i $mirror.Url --trusted-host $mirror.Host --retries 5 --timeout 60
        if ($LASTEXITCODE -eq 0) {
            $installed = $true
            break
        }
        Write-Log "依赖安装失败，切换下一个镜像..."
    }
    if (-not $installed) {
        throw "Python 依赖安装失败，请检查网络或防火墙后重试"
    }
    Write-Log "Python 依赖已就绪"
}

function Test-PythonDeps {
    param([string]$Python)
    if (-not (Test-Path $Python)) {
        return $false
    }
    try {
        & $Python -c "import fastapi, uvicorn, pydantic, pandas, numpy, sqlalchemy, httpx, dotenv, openai, anthropic, MetaTrader5" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Frontend {
    if (Test-Path (Join-Path $distDir "index.html")) {
        Write-Log "前端构建产物已存在，无需 Node.js"
        return
    }

    Write-Log "未找到前端构建产物，开始准备 Node.js 环境..."
    if (-not (Test-Command "npm")) {
        Write-Log "未找到 npm，尝试通过 winget 安装 Node.js LTS..."
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw "未找到 npm/winget，无法构建前端；请安装 Node.js 或保留 ui_layer/dist 目录"
        }
        & winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements --silent | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "winget 自动安装 Node.js 失败"
        }
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        if (-not (Test-Command "npm")) {
            throw "npm 安装后仍不可用"
        }
    }

    Write-Log "安装前端依赖并构建..."
    Push-Location $uiDir
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            Write-Log "npm install 失败，自动重试一次..."
            npm install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) {
                throw "npm install 失败"
            }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "npm run build 失败"
        }
    } finally {
        Pop-Location
    }
    Write-Log "前端构建完成"
}

function Stop-OldBackend {
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match "python|node" -and
        $_.CommandLine -like "*main.py*" -and
        ($_.CommandLine -like "*$root*" -or $_.ExecutablePath -like "$venvDir*")
    }
    foreach ($p in $procs) {
        Write-Log ("停止旧进程 PID {0}" -f $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Start-Backend {
    param([string]$Python)
    Write-Log "启动后端服务..."
    $env:PORT = "8002"
    $env:DEBUG = "0"
    $proc = Start-Process -FilePath $Python -ArgumentList "main.py" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    Write-Log ("后端进程 PID {0}" -f $proc.Id)
}

Write-Log "========== 外汇交易系统启动 =========="

if (Test-Backend) {
    Write-Log "检测到系统已在运行，直接打开前端页面"
} else {
    $python = Ensure-Venv
    if (Test-PythonDeps $python) {
        Write-Log "Python 依赖已存在，跳过安装"
    } else {
        Install-PythonDeps $python
    }
    Ensure-Frontend
    Stop-OldBackend
    Start-Backend $python

    $started = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Backend) {
            $started = $true
            break
        }
    }
    if (-not $started) {
        $errorText = ""
        if (Test-Path $stderrFile) {
            $errorText = Get-Content -LiteralPath $stderrFile -Tail 20 -ErrorAction SilentlyContinue | Out-String
        }
        Write-Log ("后端启动超时，最近错误：{0}" -f $errorText)
        throw "后端启动失败，请查看 data\startup_err.log"
    }
    Write-Log "后端启动成功"
}

if (-not $NoBrowser) {
    Start-Process $backendUrl
    Write-Log ("已打开前端页面 {0}" -f $backendUrl)
}

Write-Log "启动完成"
