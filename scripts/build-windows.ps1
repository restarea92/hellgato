param(
    [string]$Python = 'python',
    [string]$Iscc = 'ISCC.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $version = & $Python scripts/windows_release.py version
    if ($LASTEXITCODE) { throw 'Invalid Windows installer version' }
    & $Python scripts/prepare-windows.py
    if ($LASTEXITCODE) { throw 'Runtime preparation failed' }
    & './work/build-runtime/node.exe' scripts/stage-windows-sdk.mjs
    if ($LASTEXITCODE) { throw 'Packed SDK staging failed' }
    & pnpm run typecheck
    if ($LASTEXITCODE) { throw 'SDK public type checks failed' }
    & $Python native/hellgato-ui/prepare.py
    if ($LASTEXITCODE) { throw 'Native UI build dependencies failed' }
    & $Python native/hellgato-ui/build.py --output work/build-native-ui-package
    if ($LASTEXITCODE) { throw 'Native UI build failed' }
    & $Python native/hellgato-startup/build.py --test
    if ($LASTEXITCODE) { throw 'Native startup fixture or build failed' }
    & $Python native/hellgato-ui/build.py --test-host --output work/build-native-ui-test
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
    Write-Output $installer.FullName
} finally {
    Pop-Location
}
