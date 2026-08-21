#if VER != EncodeVer(6, 7, 3)
  #error Auto Research requires the locked Inno Setup 6.7.3 compiler
#endif

#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef WebView2Installer
  #error WebView2Installer is required
#endif

[Setup]
AppId={{D7E75C0A-4212-4A8E-884D-5E2F732B5EBE}
AppName=Auto Research
AppVersion={#AppVersion}
AppPublisher=Auto Research
DefaultDirName={localappdata}\Programs\Auto Research
DefaultGroupName=Auto Research
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Auto-Research-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Auto Research.exe
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=no

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebView2Installer}"; DestDir: "{tmp}"; DestName: "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Flags: deleteafterinstall

[Icons]
Name: "{autoprograms}\Auto Research"; Filename: "{app}\Auto Research.exe"
Name: "{userdesktop}\Auto Research"; Filename: "{app}\Auto Research.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "正在准备 Microsoft WebView2 运行组件…"; Flags: runhidden waituntilterminated; Check: not WebView2RuntimeInstalled
Filename: "{app}\Auto Research.exe"; Description: "启动 Auto Research"; Flags: nowait postinstall skipifsilent

[Code]
function WebView2RuntimeInstalled: Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(
    HKLM64,
    'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv',
    Version
  ) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
  if (not Result) and RegQueryStringValue(
    HKCU,
    'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv',
    Version
  ) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;

[UninstallDelete]
; User data under %LOCALAPPDATA%\Auto Research is intentionally preserved.
Type: filesandordirs; Name: "{app}"
