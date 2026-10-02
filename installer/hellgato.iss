#define AppVersion "0.0.6-beta"
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
OutputBaseFilename=Hellgato-0.0.6-beta-Setup
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

[Files]
Source: "..\dist\Hellgato\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start Hellgato automatically when I sign in to Windows"

[Icons]
Name: "{group}\Hellgato N4 Pro"; Filename: "{app}\Hellgato.exe"; IconFilename: "{app}\_internal\app\assets\icons\hellgato-front.ico"; AppUserModelID: "Hellgato.N4Pro"
Name: "{autodesktop}\Hellgato N4 Pro"; Filename: "{app}\Hellgato.exe"; Tasks: desktopicon; IconFilename: "{app}\_internal\app\assets\icons\hellgato-front.ico"; AppUserModelID: "Hellgato.N4Pro"
Name: "{userstartup}\Hellgato N4 Pro"; Filename: "{app}\Hellgato.exe"; Parameters: "--background"; Tasks: autostart; IconFilename: "{app}\_internal\app\assets\icons\hellgato-front.ico"; AppUserModelID: "Hellgato.N4Pro"

[Run]
Filename: "{app}\Hellgato.exe"; Description: "Start Hellgato"; Flags: nowait postinstall skipifsilent
