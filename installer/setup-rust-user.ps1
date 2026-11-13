param([ValidateSet('Prepare','Configure','Restart','PrepareHost','RemoveHost')][string]$Mode,[string]$InstallDir,[string]$ContextFile,[ValidateSet('0','1')][string]$Autostart='0')
$ErrorActionPreference='Stop'
$taskMode=$Mode
. (Join-Path $PSScriptRoot 'setup-user.ps1') -Mode Prepare -InstallDir $InstallDir -ContextFile $ContextFile -Autostart $Autostart

function Test-RustInstallPath([string]$Path) {
    $full=Get-FullPath $Path
    $expected=Get-FullPath (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Hellgato')
    if($full -ine $expected){throw 'Only the canonical Hellgato installation directory can be upgraded.'}
    if(Test-Path -LiteralPath $full){Assert-PlainDirectory $full}else{$current=Get-Item -LiteralPath (Split-Path $full -Parent);while($current){if($current.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Redirected installation parent'};$current=$current.Parent}}
    return $full
}

function Close-RustUpgradeApps([string]$legacy,[string]$destination,[string]$localData,[string]$ContextFile,[string]$startup) {
    $wasRunning=@(Get-InstalledProcesses $legacy $destination | Where-Object Name -eq 'StreamDeck.exe').Count -gt 0
    if($ContextFile){
        if((Test-Path -LiteralPath $ContextFile) -and (Get-Content -LiteralPath $ContextFile -Raw) -match '(?m)^StreamDeckWasRunning=1\r?$'){$wasRunning=$true}
        [IO.File]::WriteAllText($ContextFile,"[User]`r`nStartupShortcut=$(Join-Path $startup 'Hellgato Stream Dog.lnk')`r`nStreamDeckWasRunning=$([int]$wasRunning)`r`n",[Text.Encoding]::Unicode)
    }
    foreach($name in @('Hellgato.exe','HellgatoStreamDog.exe')){
        $exe=Join-Path $destination $name
        if(Test-Path -LiteralPath $exe -PathType Leaf){
            if($name -eq 'HellgatoStreamDog.exe'){$process=Start-Process -FilePath $exe -ArgumentList '--stop' -PassThru -WindowStyle Hidden;$process.WaitForExit(10000)|Out-Null}
            else{[IO.Directory]::CreateDirectory((Join-Path $localData 'Hellgato'))|Out-Null;[IO.File]::WriteAllText((Join-Path $localData 'Hellgato\launcher.stop'),'')}
        }
    }
    if(Test-Path -LiteralPath (Join-Path $legacy 'Hellgato.exe')){[IO.Directory]::CreateDirectory((Join-Path $localData 'Hellgato'))|Out-Null;[IO.File]::WriteAllText((Join-Path $localData 'Hellgato\launcher.stop'),'')}
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    do{$owners=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -ieq (Join-Path $destination 'Hellgato.exe') -or $_.ExecutablePath -ieq (Join-Path $destination 'HellgatoStreamDog.exe') -or $_.ExecutablePath -ieq (Join-Path $legacy 'Hellgato.exe')) });if(-not $owners.Count){break};Start-Sleep -Milliseconds 250}while([DateTime]::UtcNow -lt $deadline)
    if($owners.Count){throw 'Close the installed Hellgato app before continuing.'}
    Stop-InstalledLegacyWorkers $legacy $destination
    if(@(Get-InstalledProcesses $legacy $destination | Where-Object { $_.Name -in @('HellgatoWorker.exe','HellgatoMiraboxWorker.exe') }).Count){throw 'Previous Hellgato workers are still running.'}
    Close-InstalledApps $legacy $destination $localData
}

function Invoke-RustUserSetup {
    $destination=Test-RustInstallPath $InstallDir
    $localData=[Environment]::GetFolderPath('LocalApplicationData')
    $legacy=Join-Path $localData 'Programs\Hellgato'
    $startup=[Environment]::GetFolderPath('Startup')
    $shell=New-Object -ComObject WScript.Shell
    $targets=@((Join-Path $legacy 'Hellgato.exe'),(Join-Path $destination 'Hellgato.exe'),(Join-Path $destination 'HellgatoStreamDog.exe'))
    if($taskMode -in @('PrepareHost','RemoveHost')){
        $config=Get-Content -LiteralPath (Join-Path $destination 'runtime.json') -Raw|ConvertFrom-Json
        if($taskMode -eq 'PrepareHost' -and (Get-FileHash -LiteralPath $config.service.host.executable -Algorithm SHA256).Hash -ine $config.service.host.executableSha256){Write-Warning 'Untested Stream Deck build: connection is allowed; native layout preparation is unavailable.';return}
        $arguments=@($(if($taskMode -eq 'RemoveHost'){'--remove'}else{'--prepare'}),'--exe',$config.service.host.executable,'--sha256',$config.service.host.executableSha256)
        if($taskMode -eq 'PrepareHost'){$arguments+=@('--proxy',(Join-Path $destination $config.startupProxy),'--plan',(Join-Path $destination $config.startupPlan))}
        & (Join-Path $destination $config.hostWorker) @arguments
        if($LASTEXITCODE){throw 'Host startup integration operation failed'}
        return
    }
    if($taskMode -eq 'Restart'){
        if($ContextFile -and (Test-Path -LiteralPath $ContextFile) -and (Get-Content -LiteralPath $ContextFile -Raw) -match '(?m)^StreamDeckWasRunning=1\r?$'){
            $executable=Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Elgato\StreamDeck\StreamDeck.exe'
            if(-not @(Get-InstalledProcesses $legacy $destination | Where-Object Name -eq 'StreamDeck.exe').Count){Start-Process -FilePath $executable -WindowStyle Hidden|Out-Null}
        }
        return
    }
    if($taskMode -eq 'Prepare'){
        Close-RustUpgradeApps $legacy $destination $localData $ContextFile $startup
        Remove-LegacyInstallation (Read-LegacyRegistration) $localData $destination
    } else {
        $exe=Join-Path $destination 'HellgatoStreamDog.exe'
        if(-not (Test-Path -LiteralPath $exe -PathType Leaf)){throw 'New app executable is missing'}
        foreach($name in @('Hellgato N4 Pro.lnk','Hellgato Stream Dog.lnk')){
            $path=Join-Path $startup $name
            if(Test-Path -LiteralPath $path){if(-not (Test-OwnedShortcut $path $targets $shell)){throw 'A same-name startup shortcut belongs to another app'};Remove-Item -LiteralPath $path}
        }
        $run=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Run',$true)
        if($run){try{foreach($name in @('Hellgato','Hellgato Stream Dog','Hellgato N4 Pro')){$value=[string]$run.GetValue($name,'');if($value -and ($targets|Where-Object {$value.StartsWith(('"'+$_+'"'),[StringComparison]::OrdinalIgnoreCase) -or $value -ieq $_})){$run.DeleteValue($name,$false)}}}finally{$run.Dispose()}}
        if($Autostart -eq '1'){$link=$shell.CreateShortcut((Join-Path $startup 'Hellgato Stream Dog.lnk'));$link.TargetPath=$exe;$link.Arguments='--background';$link.WorkingDirectory=$destination;$link.IconLocation=Join-Path $destination 'assets\hellgato-app.ico';$link.Save()}
    }
}
if($MyInvocation.InvocationName -ne '.') {
    try{
        $state=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Hellgato'
        [IO.Directory]::CreateDirectory($state)|Out-Null
        $script:PersistentLog=Join-Path $state 'setup.log'
        Write-SetupLog ("Rust setup started: $taskMode")
        Invoke-RustUserSetup
        Write-SetupLog 'Rust setup completed.'
        exit 0
    }catch{Write-SetupLog ($_|Out-String);[Console]::Error.WriteLine($_.Exception.Message);exit 1}
}
