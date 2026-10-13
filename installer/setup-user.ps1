param(
    [ValidateSet('Initialize', 'Prepare', 'Configure')][string]$Mode,
    [string]$InstallDir,
    [string]$ContextFile,
    [ValidateSet('0', '1')][string]$Autostart = '1'
)
$ErrorActionPreference = 'Stop'
$script:UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{FB7179C2-B55A-487C-B462-C50E15F91B1B}_is1'

function Write-SetupLog([string]$Message) {
    $line = '{0:o} [{1}] {2}' -f [DateTimeOffset]::Now, $Mode, $Message
    $paths = @()
    if ($ContextFile) { $paths += $ContextFile + '.log' }
    if ($script:PersistentLog) { $paths += $script:PersistentLog }
    foreach ($path in $paths) {
        try { [IO.File]::AppendAllText($path, $line + "`r`n", [Text.Encoding]::UTF8) } catch {}
    }
}

function Get-FullPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw 'An absolute installation path is required.'
    }
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Get-LegacyUninstaller($Registration, [string]$LocalAppData) {
    if ($null -eq $Registration) { return $null }
    $expected = Get-FullPath (Join-Path $LocalAppData 'Programs\Hellgato')
    if ((Get-FullPath $Registration.InstallLocation) -ine $expected) {
        throw 'The previous installation is outside the supported migration directory.'
    }
    $match = [regex]::Match($Registration.UninstallString, '^"([^"]+)"\s*$|^(\S+)\s*$')
    if (-not $match.Success) { throw 'The previous uninstall command is not recognized.' }
    $executable = $match.Groups[1].Value
    if (-not $executable) { $executable = $match.Groups[2].Value }
    $executable = Get-FullPath $executable
    if ((Split-Path $executable -Parent) -ine $expected -or
        (Split-Path $executable -Leaf) -notmatch '^unins\d{3}\.exe$') {
        throw 'The previous uninstaller does not belong to the migration directory.'
    }
    return [pscustomobject]@{ Directory = $expected; Uninstaller = $executable }
}

function Read-LegacyRegistration {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:UninstallKey)
    if ($null -eq $key) { return $null }
    try {
        return [pscustomobject]@{
            InstallLocation = [string]$key.GetValue('InstallLocation', '')
            UninstallString = [string]$key.GetValue('UninstallString', '')
        }
    } finally { $key.Dispose() }
}

function Assert-PlainDirectory([string]$Directory) {
    $current = Get-Item -LiteralPath $Directory -Force
    while ($null -ne $current) {
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'The migration directory must not contain redirected paths.'
        }
        $current = $current.Parent
    }
    $redirected = Get-ChildItem -LiteralPath $Directory -Force -Recurse |
        Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
        Select-Object -First 1
    if ($redirected) { throw 'The migration directory contains a redirected file or folder.' }
}

function Remove-LegacyDirectory([string]$Directory, [string]$LocalAppData, [string]$Destination) {
    $resolved = Get-FullPath $Directory
    $expected = Get-FullPath (Join-Path $LocalAppData 'Programs\Hellgato')
    if ($resolved -ine $expected -or $resolved -ieq (Get-FullPath $Destination)) {
        throw 'Refusing to remove an unverified installation directory.'
    }
    if (Test-Path -LiteralPath $resolved) {
        Assert-PlainDirectory $resolved
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Remove-LegacyInstallation($Registration, [string]$LocalAppData, [string]$Destination) {
    $legacy = Get-LegacyUninstaller $Registration $LocalAppData
    if ($null -eq $legacy) { return }
    Write-SetupLog ('Checking previous directory: ' + $legacy.Directory)
    if (-not (Test-Path -LiteralPath $legacy.Directory)) {
        $current = Get-LegacyUninstaller (Read-LegacyRegistration) $LocalAppData
        if ($null -eq $current) { return }
        if ($current.Directory -ine $legacy.Directory -or $current.Uninstaller -ine $legacy.Uninstaller -or
            (Test-Path -LiteralPath $current.Directory)) {
            throw 'The previous installation changed during setup.'
        }
        [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey($script:UninstallKey, $false)
        Write-SetupLog 'Removed stale registration for an absent previous installation.'
        return
    }
    Assert-PlainDirectory $legacy.Directory
    if (-not (Test-Path -LiteralPath $legacy.Uninstaller -PathType Leaf)) {
        throw 'The previous registered uninstaller is missing.'
    }
    $process = Start-Process -FilePath $legacy.Uninstaller -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -WindowStyle Hidden -Wait -PassThru
    Write-SetupLog "Previous uninstaller result: $($process.ExitCode)"
    if ($process.ExitCode -ne 0) { throw "Previous uninstaller failed: $($process.ExitCode)" }
    if ($null -ne (Read-LegacyRegistration)) { throw 'The previous uninstall registration is still present.' }
    Remove-LegacyDirectory $legacy.Directory $LocalAppData $Destination
}

function Initialize-RestartManager {
    if ('HellgatoSetup.RestartManager' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
namespace HellgatoSetup {
    public static class RestartManager {
        [StructLayout(LayoutKind.Sequential)]
        private struct UniqueProcess {
            public uint ProcessId;
            public System.Runtime.InteropServices.ComTypes.FILETIME StartTime;
        }
        [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
        private static extern int RmStartSession(out uint session, uint flags, StringBuilder key);
        [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
        private static extern int RmRegisterResources(uint session, uint fileCount, string[] files,
            uint appCount, UniqueProcess[] apps, uint serviceCount, string[] services);
        [DllImport("rstrtmgr.dll")]
        private static extern int RmShutdown(uint session, uint flags, IntPtr callback);
        [DllImport("rstrtmgr.dll")]
        private static extern int RmEndSession(uint session);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint access, bool inherit, int processId);
        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);
        public static string Owner(int processId) {
            IntPtr process = OpenProcess(0x1000, false, processId);
            if (process == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
            try {
                IntPtr token;
                if (!OpenProcessToken(process, 8, out token)) throw new System.ComponentModel.Win32Exception();
                try {
                    using (var identity = new WindowsIdentity(token)) return identity.User.Value;
                } finally { CloseHandle(token); }
            } finally { CloseHandle(process); }
        }
        public static void Close(int[] processIds, long[] observedStartTimes) {
            if (processIds.Length == 0) return;
            var apps = new List<UniqueProcess>();
            for (int i = 0; i < processIds.Length; i++) {
                var app = new UniqueProcess();
                app.ProcessId = (uint)processIds[i];
                app.StartTime.dwLowDateTime = (int)observedStartTimes[i];
                app.StartTime.dwHighDateTime = (int)(observedStartTimes[i] >> 32);
                apps.Add(app);
            }
            uint session;
            int result = RmStartSession(out session, 0, new StringBuilder(33));
            if (result != 0) throw new System.ComponentModel.Win32Exception(result);
            try {
                result = RmRegisterResources(session, 0, null, (uint)apps.Count, apps.ToArray(), 0, null);
                if (result == 0) result = RmShutdown(session, 0, IntPtr.Zero);
                if (result != 0) throw new System.ComponentModel.Win32Exception(result);
            } finally { RmEndSession(session); }
        }
    }
}
'@
}

function Get-InstalledProcesses([string]$LegacyDirectory, [string]$Destination) {
    Initialize-RestartManager
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $session = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $paths = @(
        (Join-Path $LegacyDirectory 'Hellgato.exe'),
        (Join-Path $LegacyDirectory 'HellgatoWorker.exe'),
        (Join-Path $Destination 'Hellgato.exe'),
        (Join-Path $Destination 'HellgatoWorker.exe'),
        (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Elgato\StreamDeck\StreamDeck.exe')
    )
    foreach ($process in Get-Process -Name Hellgato,HellgatoWorker,StreamDeck -ErrorAction SilentlyContinue) {
        try {
            if ($process.SessionId -ne $session -or $paths -inotcontains $process.Path) { continue }
            $started = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
            if ([HellgatoSetup.RestartManager]::Owner($process.Id) -eq $sid) {
                [pscustomobject]@{ Name = $process.ProcessName + '.exe'; ProcessId = $process.Id; StartTime = $started }
            }
        } catch {
            if (Get-Process -Id $process.Id -ErrorAction SilentlyContinue) { throw }
            Write-SetupLog "Process $($process.Id) exited during inspection."
        }
    }
}

function Close-InstalledApps([string]$LegacyDirectory, [string]$Destination, [string]$LocalAppData) {
    $processes = @(Get-InstalledProcesses $LegacyDirectory $Destination)
    Write-SetupLog ('Closing installed apps: ' + (($processes | ForEach-Object { "$($_.Name):$($_.ProcessId)" }) -join ', '))
    if ($processes.Name -contains 'Hellgato.exe') {
        $state = Join-Path $LocalAppData 'Hellgato'
        [IO.Directory]::CreateDirectory($state) | Out-Null
        [IO.File]::WriteAllText((Join-Path $state 'launcher.stop'), '')
        Write-SetupLog 'Requested Hellgato launcher shutdown.'
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 250
            $processes = @(Get-InstalledProcesses $LegacyDirectory $Destination)
        } while (($processes.Name -contains 'Hellgato.exe') -and [DateTime]::UtcNow -lt $deadline)
    }
    $streamDeck = @($processes | Where-Object Name -eq 'StreamDeck.exe')
    if ($streamDeck.Count) {
        Initialize-RestartManager
        Write-SetupLog 'Requesting Stream Deck shutdown through Windows Restart Manager.'
        [HellgatoSetup.RestartManager]::Close([int[]]$streamDeck.ProcessId, [long[]]$streamDeck.StartTime)
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $remaining = @(Get-InstalledProcesses $LegacyDirectory $Destination)
        if (-not $remaining.Count) { Write-SetupLog 'Installed apps have stopped.'; return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Hellgato or Stream Deck is still running. Close it before retrying setup.'
}

function Test-OwnedShortcut([string]$Path, [string[]]$Targets, $Shell) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    return $Targets -icontains $Shell.CreateShortcut($Path).TargetPath
}

function Initialize-UserContext([string]$Path) {
    $file = Get-FullPath $Path
    $directory = Split-Path $file -Parent
    if ($directory -ine $PSScriptRoot -or (Split-Path $file -Leaf) -ne 'hellgato-user.ini') {
        throw 'The setup context must be next to the extracted helper.'
    }
    $authenticatedUsers = [Security.Principal.SecurityIdentifier]::new('S-1-5-11')
    # Inno's protected temp directory stays read-only to the original user.
    $acl = Get-Acl -LiteralPath $directory
    $read = [Security.AccessControl.FileSystemAccessRule]::new($authenticatedUsers,
        'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($read)
    Set-Acl -LiteralPath $directory -AclObject $acl
    foreach ($output in @($file, ($file + '.log'))) {
        [IO.File]::WriteAllText($output, '')
        $acl = Get-Acl -LiteralPath $output
        $write = [Security.AccessControl.FileSystemAccessRule]::new($authenticatedUsers, 'Write', 'Allow')
        $acl.AddAccessRule($write)
        Set-Acl -LiteralPath $output -AclObject $acl
    }
}

function Set-StartupShortcut([string]$Destination, [string]$Shortcut, [string]$OldShortcut,
    [string[]]$Targets, $Shell, [bool]$Enabled) {
    if (-not (Test-Path -LiteralPath (Join-Path $Destination 'Hellgato.exe') -PathType Leaf)) {
        throw 'The new Hellgato executable is missing.'
    }
    if (Test-OwnedShortcut $OldShortcut $Targets $Shell) { Remove-Item -LiteralPath $OldShortcut }
    if ($Enabled) {
        if ((Test-Path -LiteralPath $Shortcut) -and -not (Test-OwnedShortcut $Shortcut $Targets $Shell)) {
            throw 'A startup shortcut with this name belongs to another application.'
        }
        $link = $Shell.CreateShortcut($Shortcut)
        $link.TargetPath = Join-Path $Destination 'Hellgato.exe'
        $link.Arguments = '--background'
        $link.WorkingDirectory = $Destination
        $link.IconLocation = Join-Path $Destination '_internal\app\assets\icons\stream-dog.ico'
        $link.Save()
    } elseif (Test-OwnedShortcut $Shortcut $Targets $Shell) {
        Remove-Item -LiteralPath $Shortcut
    }
}

function Invoke-UserSetup {
    if ($Mode -eq 'Initialize') {
        Initialize-UserContext $ContextFile
        return
    }
    $destination = Get-FullPath $InstallDir
    $localData = [Environment]::GetFolderPath('LocalApplicationData')
    $legacyDirectory = Join-Path $localData 'Programs\Hellgato'
    if ($destination -ieq (Get-FullPath $legacyDirectory)) {
        throw 'Choose the new Program Files location instead of the previous per-user directory.'
    }
    $startup = [Environment]::GetFolderPath('Startup')
    $shortcut = Join-Path $startup 'Hellgato Stream Dog.lnk'
    $oldShortcut = Join-Path $startup 'Hellgato N4 Pro.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $targets = @((Join-Path $legacyDirectory 'Hellgato.exe'), (Join-Path $destination 'Hellgato.exe'))
    if ($Mode -eq 'Prepare') {
        if ((Test-Path -LiteralPath $shortcut) -and -not (Test-OwnedShortcut $shortcut $targets $shell)) {
            throw 'A startup shortcut with this name belongs to another application.'
        }
        $registration = Read-LegacyRegistration
        $legacy = Get-LegacyUninstaller $registration $localData
        Write-SetupLog ('Legacy registration: ' + ($registration | ConvertTo-Json -Compress))
        $hadAutostart = (Test-OwnedShortcut $shortcut $targets $shell) -or
            (Test-OwnedShortcut $oldShortcut $targets $shell)
        $previousInstall = ($null -ne $legacy) -or (Test-Path -LiteralPath (Join-Path $destination 'Hellgato.exe'))
        $startupDefault = [int]($hadAutostart -or -not $previousInstall)
        # Write before making changes: an inaccessible original-user context must stop setup.
        [IO.File]::WriteAllText($ContextFile, "[User]`r`nStartupShortcut=$shortcut`r`nAutostart=$startupDefault`r`n", [Text.Encoding]::Unicode)
        Close-InstalledApps $legacyDirectory $destination $localData
        Remove-LegacyInstallation $registration $localData $destination
    } elseif ($Mode -eq 'Configure') {
        Set-StartupShortcut $destination $shortcut $oldShortcut $targets $shell ($Autostart -eq '1')
    } else { throw 'A setup mode is required.' }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if ($Mode -ne 'Initialize') {
            $state = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Hellgato'
            [IO.Directory]::CreateDirectory($state) | Out-Null
            $script:PersistentLog = Join-Path $state 'setup.log'
        }
        Write-SetupLog ('Started as ' + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
        Write-SetupLog ('Context: SID={0}; 64-bit={1}; LocalAppData={2}' -f
            [Security.Principal.WindowsIdentity]::GetCurrent().User.Value, [Environment]::Is64BitProcess,
            [Environment]::GetFolderPath('LocalApplicationData'))
        Invoke-UserSetup
        Write-SetupLog 'Completed successfully.'
        exit 0
    } catch {
        Write-SetupLog (($_ | Out-String) + $_.ScriptStackTrace)
        [Console]::Error.WriteLine(($_ | Out-String) + $_.ScriptStackTrace)
        exit 1
    }
}
