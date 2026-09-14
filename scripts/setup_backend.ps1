$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Get-CompatiblePython {
    if ($env:PYTHON -and (Test-Path $env:PYTHON)) {
        return $env:PYTHON
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($version in @("3.12", "3.11", "3.10")) {
            try {
                $path = (& py "-$version" -c "import sys; print(sys.executable)") 2>$null
                if ($LASTEXITCODE -eq 0 -and $path) {
                    return $path.Trim()
                }
            } catch {}
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    throw "Python not found. Please install Python 3.10, 3.11, or 3.12."
}

$PythonExe = Get-CompatiblePython
$VersionText = (& $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ($VersionText -notin @("3.10", "3.11", "3.12")) {
    throw "Unsupported Python $VersionText. Please use Python 3.10, 3.11, or 3.12. You can set `$env:PYTHON='C:\Path\To\python.exe' before running this script."
}

if (!(Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Please edit it if needed."
}

if (!(Test-Path ".venv")) {
    & $PythonExe -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install backend requirements."
}

& ".\.venv\Scripts\python.exe" -m pip install -r requirements-yolo.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install YOLO requirements."
}

Write-Host "Backend environment is ready."
