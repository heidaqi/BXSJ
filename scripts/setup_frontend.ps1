$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location "$ProjectRoot\frontend"

npm install --registry=https://registry.npmmirror.com

Write-Host "Frontend dependencies are ready."
