param([switch]$SkipArchive)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot

# Keep third-party runtime caches inside the project while building. This avoids
# failures on restricted Windows accounts and prevents writes to user profiles.
$env:MPLCONFIGDIR = Join-Path $PSScriptRoot ".matplotlib_config"
New-Item -ItemType Directory -Force -Path $env:MPLCONFIGDIR | Out-Null

if (!(Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Development environment is missing. Run setup_desktop_dev.ps1 first." -ForegroundColor Yellow
    exit 1
}

Write-Host "[1/4] Building React frontend..." -ForegroundColor Cyan
Push-Location frontend
if (!(Test-Path "node_modules")) {
    npm ci
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm ci failed" }
}
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Frontend build failed; packaging stopped to avoid stale UI assets." }
Pop-Location

$FrontendIndex = Join-Path $PSScriptRoot "frontend\dist\index.html"
if (!(Test-Path -LiteralPath $FrontendIndex)) {
    throw "frontend/dist/index.html is missing"
}

Write-Host "[2/4] Running syntax checks and tests..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m compileall -q backend desktop
if ($LASTEXITCODE -ne 0) { throw "Python syntax check failed" }
$BuildRoot = Join-Path $PSScriptRoot "build"
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$PytestTemp = Join-Path $PSScriptRoot "build\pytest-$PID"
try {
    & ".\.venv\Scripts\python.exe" -m pytest -q --basetemp $PytestTemp -o "cache_dir=$PytestTemp\cache" tests backend\tests
    if ($LASTEXITCODE -ne 0) { throw "Project tests failed" }
} finally {
    if (Test-Path -LiteralPath $PytestTemp) {
        Remove-Item -LiteralPath $PytestTemp -Recurse -Force
    }
}

Write-Host "[3/4] Building portable onedir package..." -ForegroundColor Cyan
$BuildDir = $BuildRoot
$PackageDir = Join-Path $PSScriptRoot "dist\BXSJ"
$ZipPath = Join-Path $PSScriptRoot "dist\BXSJ-portable.zip"
foreach ($Target in @($BuildDir, $PackageDir, $ZipPath)) {
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
}
& ".\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean packaging\yolo_inspection.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$PackageDir = Join-Path $PSScriptRoot "dist\BXSJ"
$QtCore = Get-ChildItem -LiteralPath $PackageDir -Recurse -Filter "Qt6Core.dll" | Select-Object -First 1
if (!$QtCore) { throw "Qt6Core.dll is missing from package" }
$ConflictingIcu = Get-ChildItem -LiteralPath $PackageDir -Recurse -Filter "icu*.dll" |
    Where-Object { $_.Name -notmatch "74" }
if ($ConflictingIcu) {
    $Names = ($ConflictingIcu | ForEach-Object { $_.FullName }) -join "; "
    throw "Conflicting ICU DLLs found in package: $Names"
}
Write-Host "Qt/MATLAB DLL check passed: $($QtCore.FullName)" -ForegroundColor Green

Write-Host "[4/4] Creating portable archive..." -ForegroundColor Cyan
$PackageVersion = (Get-Content "frontend\package.json" -Raw | ConvertFrom-Json).version
$ZipPath = Join-Path $PSScriptRoot "dist\BXSJ-portable-v$PackageVersion.zip"
if (Test-Path $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
$RequiredFiles = @(
    "BXSJ.exe",
    "_internal\frontend\dist\index.html",
    "_internal\models\ascan_candidate_random_forest.joblib",
    "_internal\models\defect_classifier\best_PHYS_ROBUST.joblib",
    "_internal\models\paut_v2\crack_classifier.pt",
    "_internal\models\paut_v2\crack_length_model.mat",
    "_internal\models\paut_v2\refined_crack_model.mat",
    "_internal\models\paut_v2\lof_length_model.mat",
    "_internal\models\paut_v2\pore_v22_model.mat",
    "_internal\models\paut_v2\pore_v32_model.mat",
    "_internal\models\paut_v2\slag_v84_model.mat",
    "_internal\models\paut_v2\lof_slag_advisor_v1.mat",
    "_internal\algorithms\matlab\paut_v2\PAUT_complete_pipeline.m",
    "_internal\algorithms\matlab\paut_v2\paut_canonical_imaging_config.m",
    "_internal\algorithms\matlab\paut_v2\paut_reconstruct_das_shared.m",
    "_internal\algorithms\matlab\paut_v2\predict_slag_large_v84.m",
    "_internal\algorithms\matlab\paut_v2\predict_lof_slag_v1.m",
    "_internal\algorithms\matlab\TOFD_complete_pipeline.m"
)
foreach ($RelativePath in $RequiredFiles) {
    $RequiredPath = Join-Path $PackageDir $RelativePath
    if (!(Test-Path -LiteralPath $RequiredPath)) {
        throw "Portable package is missing required resource: $RelativePath"
    }
}
$SelfTest = Start-Process -FilePath (Join-Path $PackageDir "BXSJ.exe") -ArgumentList '--package-self-test' -WindowStyle Hidden -Wait -PassThru
if ($SelfTest.ExitCode -ne 0) {
    throw "Portable package runtime self-test failed with exit code $($SelfTest.ExitCode)"
}
$ForbiddenFiles = Get-ChildItem -LiteralPath $PackageDir -Recurse -Force -File | Where-Object {
    $_.Name -in @(".env", "secrets.env") -or $_.Extension -in @(".db", ".log")
}
if ($ForbiddenFiles) {
    throw "Portable package contains local configuration or user data: $($ForbiddenFiles.FullName -join ', ')"
}
if (!$SkipArchive) {
    Compress-Archive -Path "$PackageDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal
}
if (Test-Path -LiteralPath $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}

if ($SkipArchive) {
    Write-Host "Build completed: $PackageDir" -ForegroundColor Green
} else {
    Write-Host "Build completed: $ZipPath" -ForegroundColor Green
}
