$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
& "$ProjectRoot\build_desktop.ps1"
