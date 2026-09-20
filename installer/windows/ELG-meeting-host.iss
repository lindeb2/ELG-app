; ELG Meeting Host (Meeting Mike) - optional sidecar, installed only on the
; machine that records status meetings. Writes a marker into the ELG data dir;
; the ELG app shows its Record controls only when that marker resolves.
#define MyAppName "ELG Meeting Host"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyAppArch
  #define MyAppArch "x64"
#endif
#define MyAppPublisher "ELG Studio"
#define MyAppURL "https://github.com/lindeb2/ELG-app"
#define MyAppIcon "..\..\nuitka\icons\elg.ico"
#define MyPayload "..\..\build\meeting-host\" + MyAppArch

[Setup]
AppId={{7C4A1E52-9B84-4F1D-9E2A-6D5B3C81F740}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
SetupIconFile={#MyAppIcon}
DefaultDirName={localappdata}\Programs\ELG Meeting Host
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist-installers
#if MyAppArch == "arm64"
OutputBaseFilename=ELG-Meeting-Host-arm64
#else
OutputBaseFilename=ELG-Meeting-Host
#endif
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\runtime\python.exe
UninstallDisplayName={#MyAppName}
#if MyAppArch == "arm64"
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
#else
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This installs the meeting recorder used by ELG.%n%nAfter installing, open ELG and go to the Meeting App - a Record button appears there. Speech and language models (about 7 GB) download the first time you record.

[Files]
Source: "{#MyPayload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
function ElgDataDir(): String;
begin
  { Must match platformdirs user_data_dir("ELG-app", "ELG Studio") in src/storage.py }
  Result := ExpandConstant('{localappdata}') + '\ELG Studio\ELG-app';
end;

function MarkerPath(): String;
begin
  Result := ElgDataDir() + '\meeting_host.json';
end;

function JsonEscape(const Value: String): String;
var
  I: Integer;
  Ch: Char;
begin
  Result := '';
  for I := 1 to Length(Value) do
  begin
    Ch := Value[I];
    if Ch = '\' then
      Result := Result + '\\'
    else if Ch = '"' then
      Result := Result + '\"'
    else
      Result := Result + Ch;
  end;
end;

procedure WriteMarker();
var
  Contents: String;
  ExePath: String;
begin
  ExePath := ExpandConstant('{app}\runtime\python.exe');
  Contents :=
    '{' + #13#10 +
    '  "schema_version": 1,' + #13#10 +
    '  "exe_path": "' + JsonEscape(ExePath) + '",' + #13#10 +
    '  "args": ["-m", "meeting_host"],' + #13#10 +
    '  "installed_version": "' + JsonEscape('{#MyAppVersion}') + '",' + #13#10 +
    '  "install_dir": "' + JsonEscape(ExpandConstant('{app}')) + '"' + #13#10 +
    '}' + #13#10;

  if not DirExists(ElgDataDir()) then
    ForceDirectories(ElgDataDir());

  if not SaveStringToFile(MarkerPath(), Contents, False) then
    MsgBox('Meeting Host was installed but the ELG marker could not be written to:' + #13#10 +
           MarkerPath() + #13#10#13#10 +
           'ELG will not show the Record button until this file exists.',
           mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteMarker();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { Remove the marker so ELG hides the feature again. Downloaded models and
    recorded meetings live in the ELG data dir and are deliberately kept. }
  if CurUninstallStep = usUninstall then
    DeleteFile(MarkerPath());
end;
