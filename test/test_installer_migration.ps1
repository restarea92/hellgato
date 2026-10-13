$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'installer\setup-user.ps1')

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Assert-Throws([scriptblock]$Action, [string]$Message) {
    try { & $Action } catch { return }
    throw $Message
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('hellgato-installer-test-' + [Guid]::NewGuid())
[IO.Directory]::CreateDirectory($fixture) | Out-Null
try {
    $localData = Join-Path $fixture 'Local'
    $legacy = Join-Path $localData 'Programs\Hellgato'
    $destination = Join-Path $fixture 'Program Files\Hellgato'
    $uninstaller = Join-Path $legacy 'unins000.exe'
    $registration = [pscustomobject]@{ InstallLocation = $legacy + '\'; UninstallString = '"' + $uninstaller + '"' }
    $owned = Get-LegacyUninstaller $registration $localData
    Assert-True ($owned.Directory -eq $legacy -and $owned.Uninstaller -eq $uninstaller) 'Known legacy registration must be accepted.'
    Assert-True ($null -eq (Get-LegacyUninstaller $null $localData)) 'A fresh install must not invent an old registration.'

    $foreign = [pscustomobject]@{ InstallLocation = $destination; UninstallString = '"' + $uninstaller + '"' }
    Assert-Throws { Get-LegacyUninstaller $foreign $localData } 'A registration at another location must be preserved.'
    $foreign = [pscustomobject]@{ InstallLocation = $legacy; UninstallString = '"' + (Join-Path $fixture 'unins000.exe') + '"' }
    Assert-Throws { Get-LegacyUninstaller $foreign $localData } 'An uninstaller outside the old directory must be rejected.'
    $foreign = [pscustomobject]@{ InstallLocation = $legacy; UninstallString = '"' + $uninstaller + '" /other-command' }
    Assert-Throws { Get-LegacyUninstaller $foreign $localData } 'Unexpected command arguments must not be executed.'
    $foreign = [pscustomobject]@{ InstallLocation = 'Programs\Hellgato'; UninstallString = 'unins000.exe' }
    Assert-Throws { Get-LegacyUninstaller $foreign $localData } 'Relative migration paths must be rejected.'

    [IO.Directory]::CreateDirectory($legacy) | Out-Null
    [IO.File]::WriteAllText($uninstaller, 'old program')
    $settings = Join-Path $localData 'Hellgato\settings.json'
    [IO.Directory]::CreateDirectory((Split-Path $settings -Parent)) | Out-Null
    [IO.File]::WriteAllText($settings, '{"keep":true}')
    $profiles = Join-Path $fixture 'Roaming\Elgato\StreamDeck\ProfilesV3'
    [IO.Directory]::CreateDirectory($profiles) | Out-Null
    [IO.File]::WriteAllText((Join-Path $profiles 'profile'), 'keep')
    Assert-Throws { Remove-LegacyDirectory $localData $localData $destination } 'Cleanup must reject the parent data directory.'
    Assert-Throws { Remove-LegacyDirectory $legacy $localData $legacy } 'Cleanup must reject the new installation directory.'
    Remove-LegacyDirectory $legacy $localData $destination
    Assert-True (-not (Test-Path -LiteralPath $legacy)) 'Verified old program files must be removed.'
    Assert-True ((Get-Content -LiteralPath $settings -Raw) -eq '{"keep":true}') 'User settings must be preserved.'
    Assert-True (Test-Path -LiteralPath (Join-Path $profiles 'profile')) 'Stream Deck profiles must be preserved.'

    $originalUninstallKey = $script:UninstallKey
    $fixtureUninstallKey = 'Software\HellgatoInstallerTests\' + [Guid]::NewGuid()
    $script:UninstallKey = $fixtureUninstallKey
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($fixtureUninstallKey)
        try {
            $key.SetValue('InstallLocation', $registration.InstallLocation)
            $key.SetValue('UninstallString', $registration.UninstallString)
        } finally { $key.Dispose() }
        Remove-LegacyInstallation (Read-LegacyRegistration) $localData $destination
        Assert-True ($null -eq (Read-LegacyRegistration)) 'An absent old directory must allow stale registration cleanup.'
        Remove-LegacyInstallation (Read-LegacyRegistration) $localData $destination
        Assert-True (-not (Test-Path -LiteralPath $legacy)) 'Repeated setup must not recreate the previous installation.'
        Assert-True ((Get-Content -LiteralPath $settings -Raw) -eq '{"keep":true}') 'Stale registration cleanup must preserve settings.'

        [IO.Directory]::CreateDirectory($legacy) | Out-Null
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($fixtureUninstallKey)
        try {
            $key.SetValue('InstallLocation', $registration.InstallLocation)
            $key.SetValue('UninstallString', $registration.UninstallString)
        } finally { $key.Dispose() }
        Assert-Throws { Remove-LegacyInstallation (Read-LegacyRegistration) $localData $destination } 'An existing directory with a missing uninstaller must remain protected.'
        Assert-True ($null -ne (Read-LegacyRegistration)) 'A damaged but existing installation must retain its registration.'

        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($fixtureUninstallKey, $true)
        try { $key.SetValue('InstallLocation', (Join-Path $fixture 'Foreign')) } finally { $key.Dispose() }
        Assert-Throws { Remove-LegacyInstallation (Read-LegacyRegistration) $localData $destination } 'A foreign registration must never be removed, even when its directory is missing.'
        Assert-True ($null -ne (Read-LegacyRegistration)) 'Foreign registration must be preserved.'
    } finally {
        [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey($fixtureUninstallKey, $false)
        $script:UninstallKey = $originalUninstallKey
    }

    [IO.Directory]::CreateDirectory($legacy) | Out-Null
    New-Item -ItemType Junction -Path (Join-Path $legacy 'redirect') -Target $profiles | Out-Null
    Assert-Throws { Remove-LegacyDirectory $legacy $localData $destination } 'Cleanup must reject junctions.'
    Assert-True (Test-Path -LiteralPath (Join-Path $profiles 'profile')) 'Junction targets must be preserved.'
    (Get-Item -LiteralPath (Join-Path $legacy 'redirect')).Delete()

    [IO.Directory]::CreateDirectory($destination) | Out-Null
    [IO.File]::WriteAllText((Join-Path $destination 'Hellgato.exe'), 'fixture')
    $shortcut = Join-Path $fixture 'Hellgato Stream Dog.lnk'
    $oldShortcut = Join-Path $fixture 'Hellgato N4 Pro.lnk'
    $targets = @((Join-Path $legacy 'Hellgato.exe'), (Join-Path $destination 'Hellgato.exe'))
    $shell = New-Object -ComObject WScript.Shell
    $oldLink = $shell.CreateShortcut($oldShortcut)
    $oldLink.TargetPath = $targets[0]
    $oldLink.Save()
    Set-StartupShortcut $destination $shortcut $oldShortcut $targets $shell $true
    $newLink = $shell.CreateShortcut($shortcut)
    Assert-True ($newLink.TargetPath -eq $targets[1] -and $newLink.Arguments -eq '--background') 'Autostart must target the new executable.'
    Assert-True (-not (Test-Path -LiteralPath $oldShortcut)) 'Only the owned legacy startup shortcut must be removed.'
    Set-StartupShortcut $destination $shortcut $oldShortcut $targets $shell $false
    Assert-True (-not (Test-Path -LiteralPath $shortcut)) 'Unchecking autostart must remove the owned shortcut.'
    $otherLink = $shell.CreateShortcut($shortcut)
    $otherLink.TargetPath = Join-Path $fixture 'Other.exe'
    $otherLink.Save()
    Assert-Throws { Set-StartupShortcut $destination $shortcut $oldShortcut $targets $shell $true } 'An unrelated startup shortcut must not be overwritten.'
    Set-StartupShortcut $destination $shortcut $oldShortcut $targets $shell $false
    Assert-True (Test-Path -LiteralPath $shortcut) 'Unchecking must preserve unrelated startup shortcuts.'

    $contextHelper = Join-Path $fixture 'setup-user.ps1'
    Copy-Item -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'installer\setup-user.ps1') -Destination $contextHelper
    $context = Join-Path $fixture 'hellgato-user.ini'
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $contextHelper -Mode Initialize -ContextFile $context
    Assert-True ($LASTEXITCODE -eq 0) 'Context initialization must succeed in an isolated fixture.'
    Assert-True ((Test-Path -LiteralPath $context) -and (Test-Path -LiteralPath ($context + '.log'))) 'Context initialization must create exactly the expected context and log files.'
    $contextRules = (Get-Acl -LiteralPath $context).Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-11'
    }
    Assert-True (@($contextRules | Where-Object { $_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::WriteData }).Count -gt 0) 'The original user must be able to write context.'
    $helperRules = (Get-Acl -LiteralPath $contextHelper).Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-11'
    }
    Assert-True (@($helperRules | Where-Object { $_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::WriteData }).Count -eq 0) 'Context access must not grant write access to the helper.'

    Initialize-RestartManager
    Assert-True ($null -ne ('HellgatoSetup.RestartManager' -as [type])) 'Restart Manager interop must compile without closing any process.'
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    Assert-True ([HellgatoSetup.RestartManager]::Owner($PID) -eq $currentSid) 'Process ownership must use the expected user SID.'
    $script:exitedProcess = [pscustomobject]@{ Id = 2147483646; SessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId }
    $script:exitedProcess | Add-Member -MemberType ScriptProperty -Name Path -Value { throw 'The process has exited.' }
    function Get-Process {
        param($Name, $Id, $ErrorAction)
        if ($Name) { $script:exitedProcess }
    }
    try {
        Assert-True (@(Get-InstalledProcesses $legacy $destination).Count -eq 0) 'A process exiting during inspection must not fail setup.'
    } finally { Remove-Item -LiteralPath Function:\Get-Process }
    Write-Output 'Installer migration checks passed: paths, commands, stale registration, data preservation, junctions, startup ownership, context ACLs, interop, process SID and exit race.'
} finally {
    $resolved = [IO.Path]::GetFullPath($fixture)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $resolved -Leaf) -like 'hellgato-installer-test-*') {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
