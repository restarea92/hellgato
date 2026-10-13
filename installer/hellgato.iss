#define AppVersion "0.0.7-beta"
#define NativeUiSource AddBackslash(SourcePath) + "..\dist\Hellgato\_internal\native\HellgatoNativeUiV2.dll"
#define NativeUiSha256 GetSHA256OfFile(NativeUiSource)
[Setup]
AppId={{FB7179C2-B55A-487C-B462-C50E15F91B1B}
AppName=Hellgato N4 Pro
AppVersion={#AppVersion}
AppPublisher=Hellgato
DefaultDirName={localappdata}\Programs\Hellgato
DefaultGroupName=Hellgato
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist
OutputBaseFilename=Hellgato-0.0.7-beta-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\app\assets\icons\hellgato-front.ico
UninstallDisplayIcon={app}\Hellgato.exe
AppMutex=Local\Hellgato.N4Pro.Launcher
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[CustomMessages]
english.StartupInstallFailed=Hellgato is installed, but Stream Deck startup support could not be prepared. Close Stream Deck yourself, then choose Prepare Stream Deck in Hellgato to try again. Check the Hellgato logs for details.
korean.StartupInstallFailed=Hellgato가 설치되었지만 Stream Deck 시작 지원을 준비하지 못했습니다. Stream Deck을 직접 종료한 뒤 Hellgato에서 Stream Deck 준비를 눌러 다시 시도하세요. 자세한 내용은 Hellgato 로그를 확인하세요.
english.StartupRemoveFailed=Hellgato could not remove its Stream Deck startup files. Uninstallation has stopped so the cleanup helper remains available. Close Stream Deck yourself, check the Hellgato logs, then try uninstalling again.
korean.StartupRemoveFailed=Stream Deck 폴더의 Hellgato 시작 파일을 제거하지 못했습니다. 정리 도구를 유지하기 위해 제거를 중단했습니다. Stream Deck을 직접 종료하고 Hellgato 로그를 확인한 뒤 제거를 다시 시도하세요.

[Files]
Source: "..\dist\Hellgato\*"; DestDir: "{app}"; Excludes: "\_internal\native\HellgatoNativeUiV2.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#NativeUiSource}"; DestDir: "{app}\_internal\native"; Flags: ignoreversion; Check: ShouldInstallNativeUi

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start Hellgato automatically when I sign in to Windows"

[Icons]
Name: "{group}\Hellgato N4 Pro"; Filename: "{app}\Hellgato.exe"; IconFilename: "{app}\_internal\app\assets\icons\hellgato-front.ico"; AppUserModelID: "Hellgato.N4Pro"
Name: "{autodesktop}\Hellgato N4 Pro"; Filename: "{app}\Hellgato.exe"; Tasks: desktopicon; IconFilename: "{app}\_internal\app\assets\icons\hellgato-front.ico"; AppUserModelID: "Hellgato.N4Pro"
Name: "{userstartup}\Hellgato N4 Pro"; Filename: "{app}\Hellgato.exe"; Parameters: "--background"; Tasks: autostart; IconFilename: "{app}\_internal\app\assets\icons\hellgato-front.ico"; AppUserModelID: "Hellgato.N4Pro"

[Run]
Filename: "{app}\Hellgato.exe"; Description: "Start Hellgato"; Flags: nowait postinstall skipifsilent

[Code]
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
    if not RunStartupSetup('--install --elevate') then
      SuppressibleMsgBox(CustomMessage('StartupInstallFailed'), mbError, MB_OK, IDOK);
end;

function InitializeUninstall: Boolean;
begin
  Result := RunStartupSetup('--remove --elevate');
  if not Result then
    SuppressibleMsgBox(CustomMessage('StartupRemoveFailed'), mbError, MB_OK, IDOK);
end;
