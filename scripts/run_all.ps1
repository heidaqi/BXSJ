$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot

if (!(Test-Path "$ProjectRoot\.venv\Scripts\python.exe")) {
    Write-Host "Backend virtual environment not found. Run scripts\setup_all.ps1 first."
    exit 1
}

if (!(Test-Path "$ProjectRoot\frontend\node_modules")) {
    Write-Host "Frontend dependencies not found. Run scripts\setup_all.ps1 first."
    exit 1
}

$BackendCommand = "cd /d `"$ProjectRoot`" && `".\.venv\Scripts\python.exe`" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
$FrontendCommand = "cd /d `"$ProjectRoot\frontend`" && npm run dev"

Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $BackendCommand
Start-Sleep -Seconds 2
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $FrontendCommand

Write-Host "Services are starting."
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "Backend API: http://127.0.0.1:8000/docs"
