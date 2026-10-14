#include "version.iss"
#define NativeUiSource AddBackslash(SourcePath) + "..\dist\Hellgato\_internal\native\HellgatoNativeUiV2.dll"
#define NativeUiSha256 GetSHA256OfFile(NativeUiSource)
[Setup]
AppId={{FB7179C2-B55A-487C-B462-C50E15F91B1B}
AppName=Hellgato Stream Dog
AppVersion={#AppVersion}
AppPublisher=Hellgato
DefaultDirName={autopf}\Hellgato
UsePreviousAppDir=no
DefaultGroupName=Hellgato
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist
OutputBaseFilename=Hellgato-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\app\assets\icons\stream-dog.ico
UninstallDisplayIcon={app}\Hellgato.exe
CloseApplications=no
RestartApplications=no
InfoBeforeFile=limitations.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[CustomMessages]
english.CloseExistingApps=Closing existing apps
korean.CloseExistingApps=기존 앱을 종료합니다
english.PrepareFailed=Setup could not close the existing apps or remove the previous installation. Close Hellgato and Stream Deck, then retry. Details are in the setup log and your local Hellgato\setup.log file.
korean.PrepareFailed=기존 앱 종료 또는 이전 설치 정리를 완료하지 못했습니다. Hellgato와 Stream Deck을 종료한 뒤 다시 시도하세요. 자세한 내용은 설치 로그와 사용자 로컬 폴더의 Hellgato\setup.log를 확인하세요.
english.LaunchHellgato=Launch Hellgato after setup
korean.LaunchHellgato=설치 후 Hellgato 실행하기
english.AutostartHellgato=Start Hellgato when Windows starts
korean.AutostartHellgato=Windows 시작 시 Hellgato 실행하기
english.AutostartFailed=The Windows startup setting could not be saved. Check the setup log and your local Hellgato\setup.log file.
korean.AutostartFailed=Windows 시작 시 실행 설정을 저장하지 못했습니다. 설치 로그와 사용자 로컬 폴더의 Hellgato\setup.log를 확인하세요.
english.StartupInstallFailed=Hellgato is installed, but Stream Deck startup support could not be prepared. Close Stream Deck yourself, then choose Prepare Stream Deck in Hellgato to try again. Check the Hellgato logs for details.
korean.StartupInstallFailed=Hellgato가 설치되었지만 Stream Deck 시작 지원을 준비하지 못했습니다. Stream Deck을 직접 종료한 뒤 Hellgato에서 Stream Deck 준비를 눌러 다시 시도하세요. 자세한 내용은 Hellgato 로그를 확인하세요.
english.StartupRemoveFailed=Hellgato could not remove its Stream Deck startup files. Uninstallation has stopped so the cleanup helper remains available. Close Stream Deck yourself, check the Hellgato logs, then try uninstalling again.
korean.StartupRemoveFailed=Stream Deck 폴더의 Hellgato 시작 파일을 제거하지 못했습니다. 정리 도구를 유지하기 위해 제거를 중단했습니다. Stream Deck을 직접 종료하고 Hellgato 로그를 확인한 뒤 제거를 다시 시도하세요.

[Files]
Source: "setup-user.ps1"; Flags: dontcopy
Source: "..\dist\Hellgato\*"; DestDir: "{app}"; Excludes: "\_internal\native\HellgatoNativeUiV2.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#NativeUiSource}"; DestDir: "{app}\_internal\native"; Flags: ignoreversion; Check: ShouldInstallNativeUi

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Icons]
Name: "{group}\Hellgato Stream Dog"; Filename: "{app}\Hellgato.exe"; IconFilename: "{app}\_internal\app\assets\icons\stream-dog.ico"; AppUserModelID: "Hellgato.N4Pro"
Name: "{autodesktop}\Hellgato Stream Dog"; Filename: "{app}\Hellgato.exe"; Tasks: desktopicon; IconFilename: "{app}\_internal\app\assets\icons\stream-dog.ico"; AppUserModelID: "Hellgato.N4Pro"

[Registry]
Root: HKLM; Subkey: "Software\Hellgato\Installer\StartupShortcuts"; ValueType: string; ValueName: "{code:GetStartupShortcut}"; ValueData: "{code:GetStartupShortcut}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\Hellgato.exe"; Description: "{cm:LaunchHellgato}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
var
  UserContext: String;
  StartupShortcut: String;
  AutostartIndex: Integer;
  FinishOptionsAdded: Boolean;

function GetStartupShortcut(Param: String): String;
begin
  Result := StartupShortcut;
end;

function RunUserSetup(const Mode, Arguments: String): Boolean;
var
  ResultCode: Integer;
  Output: AnsiString;
begin
  ResultCode := -1;
  Result := ExecAsOriginalUser(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\setup-user.ps1') + '" -Mode ' + Mode +
    ' -InstallDir "' + ExpandConstant('{app}') + '" -ContextFile "' + UserContext + '" ' + Arguments,
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if Result then
    Result := ResultCode = 0;
  Log('User setup ' + Mode + ': result ' + IntToStr(ResultCode));
  if LoadStringFromFile(UserContext + '.log', Output) then
    Log(String(Output));
end;

function InitializeUserContext: Boolean;
var
  ResultCode: Integer;
begin
  ResultCode := -1;
  Result := ExecAndLogOutput(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\setup-user.ps1') + '" -Mode Initialize -ContextFile "' + UserContext + '"',
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode, nil);
  if Result then
    Result := ResultCode = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Progress: TOutputProgressWizardPage;
begin
  Result := '';
  ExtractTemporaryFile('setup-user.ps1');
  UserContext := ExpandConstant('{tmp}\hellgato-user.ini');
  Progress := CreateOutputProgressPage(CustomMessage('CloseExistingApps'), 'Hellgato / Stream Deck');
  Progress.Show;
  try
    if not InitializeUserContext then
      Result := FmtMessage(CustomMessage('PrepareFailed'), [UserContext + '.log'])
    else if not RunUserSetup('Prepare', '') then
      Result := FmtMessage(CustomMessage('PrepareFailed'), [UserContext + '.log'])
    else
    begin
      StartupShortcut := GetIniString('User', 'StartupShortcut', '', UserContext);
      if (ExtractFileName(StartupShortcut) <> 'Hellgato Stream Dog.lnk') or
        (Pos(':\', StartupShortcut) <> 2) then
        Result := FmtMessage(CustomMessage('PrepareFailed'), [UserContext + '.log']);
    end;
  finally
    Progress.Hide;
    Progress.Free;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Autostart: Boolean;
begin
  if (CurPageID = wpFinished) and not WizardSilent and not FinishOptionsAdded then
  begin
    Autostart := GetIniString('User', 'Autostart', '1', UserContext) = '1';
    AutostartIndex := WizardForm.RunList.AddCheckBox(CustomMessage('AutostartHellgato'), '', 0,
      Autostart, True, False, False, nil);
    FinishOptionsAdded := True;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Autostart: String;
begin
  Result := True;
  if (CurPageID = wpFinished) and FinishOptionsAdded then
  begin
    Autostart := '0';
    if WizardForm.RunList.Checked[AutostartIndex] then
      Autostart := '1';
    if not RunUserSetup('Configure', '-Autostart ' + Autostart) then
    begin
      SuppressibleMsgBox(FmtMessage(CustomMessage('AutostartFailed'), [UserContext + '.log']), mbError, MB_OK, IDOK);
      Result := False;
    end
    else
    begin
      { Only native Run entries may remain when Inno processes the checked items. }
      WizardForm.RunList.Items.Delete(AutostartIndex);
      FinishOptionsAdded := False;
    end;
  end;
end;

function ShouldInstallNativeUi: Boolean;
var
  InstalledFile: String;
begin
  Result := True;
  InstalledFile := ExpandConstant('{app}\_internal\native\HellgatoNativeUiV2.dll');
  if FileExists(InstalledFile) then
  begin
    try
      if CompareText(GetSHA256OfFile(InstalledFile), '{#NativeUiSha256}') = 0 then
      begin
        Log('Preserving identical native UI DLL: ' + InstalledFile);
        Result := False;
      end;
    except
      Log('Could not hash native UI DLL; using normal file replacement: ' + GetExceptionMessage);
    end;
  end;
end;

function RunStartupSetup(const Arguments: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{app}\HellgatoWorker.exe'),
    '--run-script "' + ExpandConstant('{app}\_internal\scripts\setup-startup.py') + '" ' + Arguments,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if Result then
    Result := ResultCode = 0;
  Log('Startup setup ' + Arguments + ': result ' + IntToStr(ResultCode));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not RunStartupSetup('--install --elevate') then
      SuppressibleMsgBox(CustomMessage('StartupInstallFailed'), mbError, MB_OK, IDOK);
    if not RunUserSetup('Configure', '-Autostart ' + GetIniString('User', 'Autostart', '1', UserContext)) then
      SuppressibleMsgBox(FmtMessage(CustomMessage('AutostartFailed'), [UserContext + '.log']), mbError, MB_OK, IDOK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Names: TArrayOfString;
  Shortcut: String;
  Shell, Link: Variant;
  I: Integer;
begin
  { Run after the standard confirmation, before any installed files are removed. }
  if CurUninstallStep = usUninstall then
  begin
    if not RunStartupSetup('--remove --elevate') then
      RaiseException(CustomMessage('StartupRemoveFailed'));
    if RegGetValueNames(HKLM, 'Software\Hellgato\Installer\StartupShortcuts', Names) then
    begin
      Shell := CreateOleObject('WScript.Shell');
      for I := 0 to GetArrayLength(Names) - 1 do
      begin
        Shortcut := Names[I];
        { Original-user paths are untrusted; remove only shortcuts targeting this installation. }
        if (ExtractFileName(Shortcut) = 'Hellgato Stream Dog.lnk') and
          (Pos(':\', Shortcut) = 2) and FileExists(Shortcut) then
        begin
          try
            Link := Shell.CreateShortcut(Shortcut);
            if CompareText(Link.TargetPath, ExpandConstant('{app}\Hellgato.exe')) = 0 then
              DeleteFile(Shortcut);
          except
            Log('Preserving unreadable startup shortcut: ' + Shortcut);
          end;
        end;
      end;
    end;
  end;
end;
