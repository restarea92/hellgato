#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef ReleaseVersion
  #define ReleaseVersion "0.2.0"
#endif
#ifndef ReleaseVersionNumeric
  #define ReleaseVersionNumeric "0.2.0.65535"
#endif
[Setup]
AppId={{FB7179C2-B55A-487C-B462-C50E15F91B1B}
AppName=Hellgato Stream Dog Beta
AppVersion={#ReleaseVersion}
VersionInfoVersion={#ReleaseVersionNumeric}
AppPublisher=Hellgato
DefaultDirName={autopf}\Hellgato
DefaultGroupName=Hellgato
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist
OutputBaseFilename=Hellgato-{#ReleaseVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\app\assets\icons\stream-dog.ico
UninstallDisplayIcon={app}\assets\hellgato-app.ico
CloseApplications=no
RestartApplications=no
UsePreviousAppDir=no
DisableDirPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "setup-user.ps1"; Flags: dontcopy
Source: "setup-rust-user.ps1"; Flags: dontcopy
Source: "{#BundleDir}\HellgatoStreamDog.exe"; Flags: dontcopy
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\app\assets\icons\hellgato-app.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"; Check: LegacyCleanup
Type: files; Name: "{app}\Hellgato.exe"; Check: LegacyCleanup
Type: files; Name: "{app}\HellgatoWorker.exe"; Check: LegacyCleanup
Type: files; Name: "{app}\HellgatoMiraboxWorker.exe"; Check: LegacyCleanup

[Tasks]
Name: "desktopicon"; Description: "바탕 화면 바로가기 만들기"; Flags: unchecked
Name: "autostart"; Description: "Windows 시작 시 트레이로 실행"; Flags: unchecked

[Icons]
Name: "{group}\Hellgato Stream Dog"; Filename: "{app}\HellgatoStreamDog.exe"; IconFilename: "{app}\assets\hellgato-app.ico"
Name: "{autodesktop}\Hellgato Stream Dog"; Filename: "{app}\HellgatoStreamDog.exe"; IconFilename: "{app}\assets\hellgato-app.ico"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "Software\Hellgato\Installer\StartupShortcuts"; ValueType: string; ValueName: "{code:GetStartupShortcut}"; ValueData: "{code:GetStartupShortcut}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\HellgatoStreamDog.exe"; Description: "Hellgato Stream Dog 실행"; Flags: postinstall nowait skipifsilent runasoriginaluser

[Code]
var
  CleanLegacy: Boolean;
  UserContext: String;
  StartupShortcut: String;
function GetStartupShortcut(Param: String): String;
begin Result := StartupShortcut; end;
function LegacyCleanup: Boolean;
begin
  Result := CleanLegacy;
end;

function UserSetup(const Mode: String): Boolean;
var Code: Integer;
begin
  Result := ExecAsOriginalUser(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\setup-rust-user.ps1') +
    '" -Mode ' + Mode + ' -InstallDir "' + ExpandConstant('{app}') + '" -ContextFile "' + UserContext + '" -Autostart ' + IntToStr(Ord(WizardIsTaskSelected('autostart'))),
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code);
  if Result then Result := Code = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var RegisteredDir: String; Code: Integer;
begin
  Result := '';
  ExtractTemporaryFile('setup-user.ps1');
  ExtractTemporaryFile('setup-rust-user.ps1');
  ExtractTemporaryFile('HellgatoStreamDog.exe');
  if not ExecAsOriginalUser(ExpandConstant('{tmp}\HellgatoStreamDog.exe'),'--stop',ExpandConstant('{tmp}'),SW_HIDE,ewWaitUntilTerminated,Code) or (Code <> 0) then begin Result := '기존 Rust 앱을 종료하지 못했습니다.'; exit; end;
  UserContext := ExpandConstant('{tmp}\hellgato-user.ini');
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\setup-user.ps1') + '" -Mode Initialize -ContextFile "' + UserContext + '"',ExpandConstant('{tmp}'),SW_HIDE,ewWaitUntilTerminated,Code) or (Code <> 0) then begin Result := '사용자 설치 정보를 준비하지 못했습니다.'; exit; end;
  CleanLegacy := DirExists(ExpandConstant('{app}\_internal')) or FileExists(ExpandConstant('{app}\Hellgato.exe')) or FileExists(ExpandConstant('{app}\HellgatoWorker.exe')) or FileExists(ExpandConstant('{app}\HellgatoMiraboxWorker.exe'));
  if CleanLegacy then begin
    if not RegQueryStringValue(HKLM64,'Software\Microsoft\Windows\CurrentVersion\Uninstall\{FB7179C2-B55A-487C-B462-C50E15F91B1B}_is1','InstallLocation',RegisteredDir) then begin
      Result := '기존 Hellgato 설치 소유권을 확인하지 못했습니다. 기존 제거 프로그램으로 먼저 제거해 주세요. 설정과 프로필은 유지됩니다.';
      exit;
    end;
    if CompareText(RemoveBackslashUnlessRoot(RegisteredDir),RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) <> 0 then begin
      Result := '등록된 기존 설치 경로가 다릅니다. 기존 제거 프로그램으로 먼저 제거해 주세요.'; exit;
    end;
  end;
  if not UserSetup('Prepare') then Result := 'Hellgato를 종료하고 Stream Deck을 직접 닫은 뒤 다시 시도해 주세요. 사용자 설정과 Stream Deck 프로필은 삭제하지 않습니다.';
  StartupShortcut := GetIniString('User','StartupShortcut','',UserContext);
  if (ExtractFileName(StartupShortcut) <> 'Hellgato Stream Dog.lnk') or (Pos(':\',StartupShortcut) <> 2) then Result := '자동 실행 바로가기 경로를 확인하지 못했습니다.';
end;

procedure CurStepChanged(Step: TSetupStep);
var Code: Integer;
begin
  if Step = ssPostInstall then begin
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\tools\setup-rust-user.ps1') + '" -Mode PrepareHost -InstallDir "' + ExpandConstant('{app}') + '"',ExpandConstant('{app}'),SW_HIDE,ewWaitUntilTerminated,Code) or (Code <> 0) then SuppressibleMsgBox('Stream Deck 시작 지원 준비에 실패했습니다. 앱의 설정에서 Stream Deck 준비를 실행해 주세요.',mbError,MB_OK,IDOK);
    if not UserSetup('Configure') then SuppressibleMsgBox('자동 실행 바로가기 설정에 실패했습니다. 앱은 설치되었습니다.',mbError,MB_OK,IDOK);
  end;
end;

procedure CurUninstallStepChanged(Step: TUninstallStep);
var Code,I: Integer; Names: TArrayOfString; Shell,Link: Variant; Shortcut: String;
begin
  if Step = usUninstall then begin
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\tools\setup-rust-user.ps1') + '" -Mode RemoveHost -InstallDir "' + ExpandConstant('{app}') + '"',ExpandConstant('{app}'),SW_HIDE,ewWaitUntilTerminated,Code) or (Code <> 0) then RaiseException('Stream Deck을 직접 종료한 뒤 제거를 다시 실행해 주세요.');
    if RegGetValueNames(HKLM,'Software\Hellgato\Installer\StartupShortcuts',Names) then begin
      Shell := CreateOleObject('WScript.Shell');
      for I := 0 to GetArrayLength(Names)-1 do begin
        Shortcut := Names[I];
        if (ExtractFileName(Shortcut) = 'Hellgato Stream Dog.lnk') and (Pos(':\',Shortcut) = 2) and FileExists(Shortcut) then begin
          Link := Shell.CreateShortcut(Shortcut);
          if CompareText(Link.TargetPath,ExpandConstant('{app}\HellgatoStreamDog.exe')) = 0 then DeleteFile(Shortcut);
        end;
      end;
    end;
  end;
end;

[UninstallRun]
Filename: "{app}\HellgatoStreamDog.exe"; Parameters: "--stop"; Flags: runhidden waituntilterminated
