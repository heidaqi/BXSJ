$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot

if (!(Test-Path ".venv\Scripts\python.exe")) {
    $Python = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
    if ($Python -eq "py") { & py -3.10 -m venv .venv } else { & $Python -m venv .venv }
}

Write-Host "[1/3] Installing backend and desktop dependencies..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

Write-Host "[2/3] Installing application dependencies..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-desktop.txt -r requirements-image-compat.txt
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }

Write-Host "[3/3] Installing frontend dependencies..." -ForegroundColor Cyan
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
} finally {
    Pop-Location
}

Write-Host "Setup completed. Run run_desktop_dev.ps1 for normal development." -ForegroundColor Green
