param(
    [string]$RuntimeZip = $env:BXSJ_RUNTIME_ZIP,
    [string]$RuntimeLicense = $env:BXSJ_RUNTIME_LICENSE,
    [string]$InnoCompiler = $env:BXSJ_INNO_COMPILER,
    [switch]$ReuseDesktopPackage,
    [switch]$KeepStage
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location $PSScriptRoot

foreach ($Required in @($RuntimeZip, $RuntimeLicense, $InnoCompiler)) {
    if (!(Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Offline build dependency is missing: $Required"
    }
}

$PackageDir = Join-Path $PSScriptRoot 'dist\BXSJ'
$WorkerSource = Join-Path $PSScriptRoot 'packaging\PautOfflineWorker.m'
$WorkerBinary = Join-Path $PSScriptRoot 'packaging\runtime_build\PautOfflineWorker.exe'
$NewestMatlabSource = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'algorithms\matlab') -Filter '*.m' -File -Recurse |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if (!(Test-Path -LiteralPath $WorkerBinary -PathType Leaf)) {
    throw 'Compiled PAUT worker is missing. Rebuild it with MATLAB Compiler before packaging.'
}
$WorkerInputTime = @(
    (Get-Item -LiteralPath $WorkerSource).LastWriteTimeUtc,
    $NewestMatlabSource.LastWriteTimeUtc
) | Sort-Object -Descending | Select-Object -First 1
if ((Get-Item -LiteralPath $WorkerBinary).LastWriteTimeUtc -lt $WorkerInputTime) {
    throw 'Compiled PAUT worker is older than the MATLAB source. Rebuild it before packaging.'
}
$env:PAUT_OFFLINE_BUILD = '1'
try {
    if (!$ReuseDesktopPackage) {
        & (Join-Path $PSScriptRoot 'build_desktop.ps1') -SkipArchive
        if ($LASTEXITCODE -ne 0) { throw 'Desktop package build failed.' }
    }
} finally {
    Remove-Item Env:PAUT_OFFLINE_BUILD -ErrorAction SilentlyContinue
}
if (!(Test-Path -LiteralPath (Join-Path $PackageDir 'BXSJ.exe') -PathType Leaf)) {
    throw 'Desktop package is missing. Build without -ReuseDesktopPackage first.'
}
$PackagedWorker = Join-Path $PackageDir '_internal\runtime_worker\PautOfflineWorker.exe'
if (!(Test-Path -LiteralPath $PackagedWorker -PathType Leaf)) {
    throw 'Desktop package does not contain the compiled PAUT worker.'
}
if ((Get-FileHash -LiteralPath $PackagedWorker -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $WorkerBinary -Algorithm SHA256).Hash) {
    throw 'Desktop package contains a stale PAUT worker. Rebuild the desktop package before creating the installer.'
}
$SelfTest = Start-Process -FilePath (Join-Path $PackageDir 'BXSJ.exe') -ArgumentList '--package-self-test' -WindowStyle Hidden -Wait -PassThru
if ($SelfTest.ExitCode -ne 0) {
    throw "Desktop package self-test failed: $($SelfTest.ExitCode)"
}

$StageRoot = Join-Path $PSScriptRoot 'dist\offline-stage'
$StageApp = Join-Path $StageRoot 'BXSJ'
$InstallerDir = Join-Path $PSScriptRoot 'dist\offline-installer'

if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
Copy-Item -LiteralPath $PackageDir -Destination $StageApp -Recurse

$Worker = Join-Path $StageApp '_internal\runtime_worker\PautOfflineWorker.exe'
if (!(Test-Path -LiteralPath $Worker -PathType Leaf)) {
    throw 'Offline stage does not contain the compiled PAUT worker.'
}
$Forbidden = Get-ChildItem -LiteralPath $StageApp -Recurse -Force -File | Where-Object {
    $_.Name -in @('.env', 'secrets.env') -or $_.Extension -in @('.db', '.log')
}
if ($Forbidden) {
    throw "Offline stage contains private or generated files: $($Forbidden.FullName -join ', ')"
}

if (Test-Path -LiteralPath $InstallerDir) {
    Remove-Item -LiteralPath $InstallerDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $InstallerDir | Out-Null

& $InnoCompiler "/DRuntimeZip=$RuntimeZip" "/DRuntimeLicense=$RuntimeLicense" (Join-Path $PSScriptRoot 'packaging\offline_setup.iss')
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$Setup = Join-Path $InstallerDir 'BXSJ-Offline-Setup.exe'
$Slices = @(Get-ChildItem -LiteralPath $InstallerDir -Filter 'BXSJ-Offline-Setup-*.bin' -File)
if (!(Test-Path -LiteralPath $Setup -PathType Leaf) -or !$Slices.Count) {
    throw 'Offline installer output is incomplete.'
}

if (!$KeepStage -and (Test-Path -LiteralPath $StageRoot)) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}

Write-Host "Offline installer completed: $InstallerDir" -ForegroundColor Green
