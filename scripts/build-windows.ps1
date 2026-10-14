param(
    [string]$Python = 'python',
    [string]$Iscc = 'ISCC.exe',
    [string]$ReleasePlan = '',
    [string]$SourceChannel = ''
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
    & pnpm run typecheck
    if ($LASTEXITCODE) { throw 'SDK public type checks failed' }
    & $Python host-integrations/streamdeck/windows/native/ui/prepare.py
    if ($LASTEXITCODE) { throw 'Native UI build dependencies failed' }
    & $Python host-integrations/streamdeck/windows/native/ui/build.py --output work/build-native-ui-package
    if ($LASTEXITCODE) { throw 'Native UI build failed' }
    & $Python host-integrations/streamdeck/windows/native/startup/build.py --test
    if ($LASTEXITCODE) { throw 'Native startup fixture or build failed' }
    & $Python host-integrations/streamdeck/windows/native/ui/build.py --test-host --output work/build-native-ui-test
    if ($LASTEXITCODE) { throw 'Native UI fixture build failed' }
    $oldPath = $env:PATH
    $oldQtPlugins = $env:QT_PLUGIN_PATH
    try {
        $qtRoot = Join-Path $projectRoot 'work/toolchain/qt-6.5.3/6.5.3/msvc2019_64'
        $env:PATH = (Join-Path $qtRoot 'bin') + ';' + $env:PATH
        $env:QT_PLUGIN_PATH = Join-Path $qtRoot 'plugins'
        $fixture = Start-Process -FilePath (Join-Path $projectRoot 'work/build-native-ui-test/HellgatoNativeUiTest.exe') -ArgumentList '-platform','offscreen' -NoNewWindow -PassThru -RedirectStandardOutput 'work/build-native-ui-test/fixture-results.txt' -RedirectStandardError 'work/build-native-ui-test/fixture-stderr.txt'
        if (-not $fixture.WaitForExit(45000)) { $fixture.Kill(); throw 'Native UI fixture timed out' }
        $fixture.WaitForExit()
        if ($fixture.ExitCode -ne 0) { throw 'Native UI fixture failed' }
        Get-Content 'work/build-native-ui-test/fixture-results.txt'
    } finally {
        $env:PATH = $oldPath
        $env:QT_PLUGIN_PATH = $oldQtPlugins
    }
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath work/pyinstaller installer/hellgato.spec
    if ($LASTEXITCODE) { throw 'Executable build failed' }
    & $Iscc /Q installer/hellgato.iss
    if ($LASTEXITCODE) { throw 'Installer build failed' }
    $installer = Get-Item "dist/Hellgato-$version-Setup.exe"
    $checksum = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$checksum  $($installer.Name)" | Set-Content -LiteralPath "$($installer.FullName).sha256" -Encoding ascii
    # An isolated CI-only portable trial. It never changes the installed engine.
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath work/pyinstaller-worker installer/mirabox-worker.spec
    if ($LASTEXITCODE) { throw 'Frozen IPC worker build failed' }
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath work/pyinstaller-candidate installer/ipc-candidate.spec
    if ($LASTEXITCODE) { throw 'Candidate supervisor build failed' }
    & $Python scripts/stage-ipc-candidate.py
    if ($LASTEXITCODE) { throw 'Immutable IPC candidate staging failed' }
    $selfTestOutput = Join-Path $projectRoot 'work/ipc-candidate-self-test'
    if (Test-Path -LiteralPath $selfTestOutput) { Remove-Item -Recurse -Force -LiteralPath $selfTestOutput }
    $selfTest = Start-Process -FilePath (Join-Path $projectRoot 'dist/Hellgato-Ipc-Candidate/HellgatoIpcTest.exe') -ArgumentList '--self-test', '--output', "`"$selfTestOutput`"" -NoNewWindow -PassThru -RedirectStandardOutput 'work/ipc-candidate-self-test-stdout.txt' -RedirectStandardError 'work/ipc-candidate-self-test-stderr.txt'
    if (-not $selfTest.WaitForExit(120000)) { $selfTest.Kill(); throw 'Frozen IPC candidate self-test timed out' }
    $selfTest.WaitForExit()
    Get-Content 'work/ipc-candidate-self-test-stdout.txt'
    if ($selfTest.ExitCode -ne 0) {
        Get-Content 'work/ipc-candidate-self-test-stderr.txt'
        throw 'Frozen IPC candidate self-test failed'
    }
    & $Python scripts/stage-ipc-candidate.py --verify dist/Hellgato-Ipc-Candidate
    if ($LASTEXITCODE) { throw 'Candidate changed during self-test' }
    & $Python scripts/stage-ipc-candidate.py --archive dist/Hellgato-Ipc-Candidate
    if ($LASTEXITCODE) { throw 'Candidate archive creation failed' }
    Write-Output $installer.FullName
} finally {
    Pop-Location
}
