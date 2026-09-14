$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot

if (!(Test-Path ".venv\Scripts\python.exe") -or !(Test-Path "frontend\node_modules")) {
    Write-Host "Development environment is missing. Run setup_desktop_dev.ps1 first." -ForegroundColor Yellow
    exit 1
}

$DistIndex = Get-Item "frontend\dist\index.html" -ErrorAction SilentlyContinue
$NewestSource = Get-ChildItem "frontend\src", "frontend\index.html", "frontend\package-lock.json" -Recurse -File |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (!$DistIndex -or $NewestSource.LastWriteTime -gt $DistIndex.LastWriteTime) {
    Write-Host "Frontend sources changed. Building..." -ForegroundColor Cyan
    Push-Location frontend
    npm run build
    Pop-Location
}

$env:YOLO_DESKTOP_MODE = "1"
& ".\.venv\Scripts\python.exe" -m desktop.main
