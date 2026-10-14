param(
    [string]$Python = 'python',
    [string]$Iscc = 'ISCC.exe',
    [string]$ReleasePlan = '',
    [string]$SourceChannel = '',
    [switch]$UsePreparedNative
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $planArgs = @()
    if ($ReleasePlan -and $SourceChannel) { throw 'Release plan and source channel are mutually exclusive' }
    if ($SourceChannel -and $SourceChannel -ne 'beta') { throw 'Invalid source channel' }
    if ($SourceChannel) { $planArgs = @('--source-channel', 'beta') }
    if ($ReleasePlan) {
        $planArgs = @('--plan', $ReleasePlan)
        & node scripts/sync-release-metadata.mjs --check @planArgs
        if ($LASTEXITCODE) { throw 'Release-plan metadata check failed' }
    }
    if ($SourceChannel) {
        & node scripts/sync-release-metadata.mjs --check @planArgs
        if ($LASTEXITCODE) { throw 'Source-channel metadata check failed' }
    }
    $version = & $Python scripts/windows_release.py version @planArgs
    if ($LASTEXITCODE) { throw 'Invalid Windows installer version' }
    & $Python scripts/prepare-windows.py
    if ($LASTEXITCODE) { throw 'Runtime preparation failed' }
    & './work/build-runtime/node.exe' scripts/stage-windows-sdk.mjs @planArgs
    if ($LASTEXITCODE) { throw 'Packed SDK staging failed' }
    if (-not $UsePreparedNative) {
        & $Python host-integrations/streamdeck/windows/native/ui/prepare.py
        if ($LASTEXITCODE) { throw 'Native UI build dependencies failed' }
        & $Python host-integrations/streamdeck/windows/native/ui/build.py --output work/build-native-ui-package
        if ($LASTEXITCODE) { throw 'Native UI build failed' }
        & $Python host-integrations/streamdeck/windows/native/startup/build.py
        if ($LASTEXITCODE) { throw 'Native startup build failed' }
    }
    foreach ($nativeFile in @('work/build-native-startup/version.dll', 'work/build-native-ui-package/HellgatoNativeUiV2.dll')) {
        if (-not (Test-Path -LiteralPath $nativeFile -PathType Leaf)) { throw "Missing native component: $nativeFile" }
    }
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath work/pyinstaller-worker installer/mirabox-worker.spec
    if ($LASTEXITCODE) { throw 'USB worker build failed' }
    & $Python scripts/stage-product-worker.py
    if ($LASTEXITCODE) { throw 'USB worker staging failed' }
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath work/pyinstaller installer/hellgato.spec
    if ($LASTEXITCODE) { throw 'Executable build failed' }
    & $Iscc /Q installer/hellgato.iss
    if ($LASTEXITCODE) { throw 'Installer build failed' }
    $installer = Get-Item "dist/Hellgato-$version-Setup.exe"
    $checksum = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$checksum  $($installer.Name)" | Set-Content -LiteralPath "$($installer.FullName).sha256" -Encoding ascii
    Write-Output $installer.FullName
} finally {
    Pop-Location
}
