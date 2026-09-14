$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "Step 1/2: setting up backend..."
powershell -ExecutionPolicy Bypass -File "$ProjectRoot\scripts\setup_backend.ps1"

Write-Host "Step 2/2: setting up frontend..."
powershell -ExecutionPolicy Bypass -File "$ProjectRoot\scripts\setup_frontend.ps1"

Write-Host ""
Write-Host "Setup completed."
Write-Host "Next time, run:"
Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\run_all.ps1"
