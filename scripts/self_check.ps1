$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Test-Path ".venv\Scripts\python.exe") {
    & ".\.venv\Scripts\python.exe" -m compileall backend
    & ".\.venv\Scripts\python.exe" -m backend.app.self_check
} else {
    python -m compileall backend
    python -m backend.app.self_check
}

Write-Host "Python syntax check passed."
