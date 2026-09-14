param([Parameter(Mandatory=$true)][string]$AppDir)
$ErrorActionPreference = 'Stop'
$AppDir = [IO.Path]::GetFullPath($AppDir)
$RuntimeDir = Join-Path $AppDir 'runtime'
$Cache = Join-Path $AppDir 'data\runtime_install'

function Find-RuntimeRoot([string]$BaseDir) {
    $Candidates = @(
        (Join-Path $BaseDir 'R2025b'),
        (Join-Path $BaseDir 'R2025b\R2025b'),
        $BaseDir
    )
    foreach ($Candidate in $Candidates) {
        $Dll = Join-Path $Candidate 'runtime\win64\mclmcrrt25_2.dll'
        $Bin = Join-Path $Candidate 'bin\win64'
        if ((Test-Path -LiteralPath $Dll) -and (Test-Path -LiteralPath $Bin -PathType Container)) {
            return [IO.Path]::GetFullPath($Candidate)
        }
    }
    if (Test-Path -LiteralPath $BaseDir) {
        $Dll = Get-ChildItem -LiteralPath $BaseDir -Filter 'mclmcrrt25_2.dll' -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Directory.Name -eq 'win64' -and $_.Directory.Parent.Name -eq 'runtime' } |
            Select-Object -First 1
        if ($Dll) {
            $Root = $Dll.Directory.Parent.Parent.FullName
            if (Test-Path -LiteralPath (Join-Path $Root 'bin\win64') -PathType Container) {
                return [IO.Path]::GetFullPath($Root)
            }
        }
    }
    return $null
}

New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$env:TEMP = $Cache
$env:TMP = $Cache
$Log = Join-Path $Cache 'install.log'
try {
    $ExistingRuntime = Find-RuntimeRoot $RuntimeDir
    if ($ExistingRuntime) {
        Set-Content -LiteralPath (Join-Path $RuntimeDir 'runtime-root.txt') -Value $ExistingRuntime -Encoding UTF8
        exit 0
    }
    if (![Environment]::Is64BitOperatingSystem) {
        throw 'MATLAB Runtime R2025b requires 64-bit Windows.'
    }
    $Drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($AppDir))
    $MinimumFreeBytes = 18GB
    if ($Drive.AvailableFreeSpace -lt $MinimumFreeBytes) {
        $FreeGB = [Math]::Round($Drive.AvailableFreeSpace / 1GB, 1)
        throw "Insufficient free space on $($Drive.Name): $FreeGB GB available, at least 18 GB is required after application files are copied."
    }
    $Archive = Join-Path $AppDir 'runtime-installer.zip'
    if (!(Test-Path -LiteralPath $Archive)) { throw 'Offline Runtime archive is missing.' }
    $Extracted = Join-Path $Cache 'installer'
    if (!(Test-Path (Join-Path $Extracted 'setup.exe'))) {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Extracted)
    }
    $Setup = Join-Path $Extracted 'setup.exe'
    if (!(Test-Path -LiteralPath $Setup)) { throw 'Runtime setup.exe is missing.' }
    $Process = Start-Process -FilePath $Setup -ArgumentList @('-mode','silent','-agreeToLicense','yes','-destinationFolder',('"' + $RuntimeDir + '"'),'-outputFile',('"' + $Log + '"')) -WindowStyle Hidden -Wait -PassThru
    if ($Process.ExitCode -notin @(0,3010)) { throw "Runtime installer failed: $($Process.ExitCode). See $Log" }
    $InstalledRuntime = Find-RuntimeRoot $RuntimeDir
    if (!$InstalledRuntime) { throw "Runtime verification failed. See $Log" }
    Set-Content -LiteralPath (Join-Path $RuntimeDir 'runtime-root.txt') -Value $InstalledRuntime -Encoding UTF8
    # Only remove this installer's own staging directory, never application data.
    $Resolved = [IO.Path]::GetFullPath($Extracted)
    if (!$Resolved.StartsWith($AppDir + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid cleanup path.' }
    Remove-Item -LiteralPath $Resolved -Recurse -Force
    Remove-Item -LiteralPath $Archive -Force
    exit 0
} catch {
    $Message = $_ | Out-String
    $Message | Add-Content -LiteralPath (Join-Path $Cache 'error.log') -Encoding UTF8
    $Message | Add-Content -LiteralPath (Join-Path $AppDir 'installation-error.log') -Encoding UTF8
    exit 1
}
