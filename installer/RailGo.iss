; Inno Setup script for unpackaged WinUI 3 desktop installer.

#ifndef MyAppName
  #define MyAppName "RailGo"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "AZ Studio"
#endif
#ifndef MyPublishDir
  #define MyPublishDir "..\artifacts\publish\win-x64"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\artifacts\installer"
#endif
#ifndef MyAppExeName
  #define MyAppExeName "RailGo.exe"
#endif
#ifndef MyWebViewBootstrapper
  #define MyWebViewBootstrapper "..\artifacts\prerequisites\MicrosoftEdgeWebview2Setup.exe"
#endif

[Setup]
AppId={{4F9D4E1E-2A96-43B1-9A28-6E2BCB7E53C5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\RailGo\Assets\WindowIcon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "{#MyPublishDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyWebViewBootstrapper}"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装 Microsoft Edge WebView2 Runtime..."; Flags: waituntilterminated runhidden; Check: not IsWebView2Installed
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function HasWebView2Executable(const ApplicationRoot: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := FindFirst(AddBackslash(ApplicationRoot) + '*\msedgewebview2.exe', FindRec);
  if Result then
    FindClose(FindRec);
end;

function IsWebView2Installed: Boolean;
begin
  Result :=
    HasWebView2Executable(ExpandConstant('{pf32}\Microsoft\EdgeWebView\Application')) or
    HasWebView2Executable(ExpandConstant('{localappdata}\Microsoft\EdgeWebView\Application'));
end;
