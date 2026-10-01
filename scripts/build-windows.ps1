param(
    [string]$Python = 'python',
    [string]$Iscc = 'ISCC.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    & $Python scripts/prepare-windows.py
    if ($LASTEXITCODE) { throw 'Runtime preparation failed' }
    & './work/build-runtime/node.exe' scripts/prepare-cora.mjs
    if ($LASTEXITCODE) { throw 'CORA preparation failed' }
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath work/pyinstaller installer/hellgato.spec
    if ($LASTEXITCODE) { throw 'Executable build failed' }
    & $Iscc /Q installer/hellgato.iss
    if ($LASTEXITCODE) { throw 'Installer build failed' }
    $installer = Get-Item 'dist/Hellgato-0.0.5-beta-Setup.exe'
    $checksum = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$checksum  $($installer.Name)" | Set-Content -LiteralPath "$($installer.FullName).sha256" -Encoding ascii
    Write-Output $installer.FullName
} finally {
    Pop-Location
}
