#ifndef RuntimeZip
  #error RuntimeZip must point to the verified offline Runtime ZIP
#endif
#ifndef RuntimeLicense
  #error RuntimeLicense must point to the Runtime license agreement
#endif
[Setup]
AppId=PAUTYoloInspectionOffline
AppName=BXSJ
AppVersion=1.0.2
DefaultDirName={code:DefaultInstallDir}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\offline-installer
OutputBaseFilename=BXSJ-Offline-Setup
LicenseFile={#RuntimeLicense}
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=2000000000
ExtraDiskSpaceRequired=18000000000
UninstallDisplayIcon={app}\BXSJ.exe
WizardStyle=modern

[Files]
Source: "..\dist\offline-stage\BXSJ\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "*.log,*.db,.env,secrets.env"
Source: "install_runtime.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "{#RuntimeZip}"; DestDir: "{app}"; DestName: "runtime-installer.zip"; Flags: ignoreversion nocompression; Check: ShouldInstallRuntime

[Icons]
Name: "{autodesktop}\BXSJ"; Filename: "{app}\BXSJ.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\BXSJ"; Filename: "{app}\BXSJ.exe"; WorkingDir: "{app}"

[Code]
function ShouldInstallRuntime: Boolean;
begin
  { Hidden QA switch: production installs never pass NORUNTIME and always install Runtime. }
  Result := CompareText(ExpandConstant('{param:NORUNTIME|0}'), '1') <> 0;
end;

function DefaultInstallDir(Param: String): String;
begin
  Result := ExtractFileDrive(ExpandConstant('{src}')) + '\BXSJ';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and
     (CompareText(ExtractFileDrive(WizardDirValue), ExpandConstant('{sd}')) = 0) then
    Result := MsgBox('The selected folder is on the system drive. Runtime requires substantial disk space. Continue on this drive?', mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if (CurStep = ssPostInstall) and ShouldInstallRuntime then begin
    WizardForm.StatusLabel.Caption := 'Installing MATLAB Runtime on the selected drive. Please wait...';
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer\install_runtime.ps1') + '" -AppDir "' + ExpandConstant('{app}') + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('Unable to start Runtime installation.');
    if Code <> 0 then
      RaiseException('Runtime installation failed. See installation-error.log and data\runtime_install in the selected application folder.');
  end;
end;
