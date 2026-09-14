$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (!(Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Virtual environment not found. Run scripts\setup_backend.ps1 first."
    exit 1
}

& ".\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
