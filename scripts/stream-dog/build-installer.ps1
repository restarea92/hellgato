param([Parameter(Mandatory=$true)][string]$Bundle,[Parameter(Mandatory=$true)][string]$Iscc,[Alias('BetaVersion')][string]$Version)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if(-not $Version){$Version=(Get-Content -LiteralPath (Join-Path $projectRoot 'packages/windows-release/package.json') -Raw|ConvertFrom-Json).version}
$bundlePath=(Resolve-Path -LiteralPath $Bundle).Path
if($Version -notmatch '^(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$'){throw 'Use a canonical release version'}
$revision=if($Matches[4]){[int]$Matches[4]+1}else{65535}
$numeric="$($Matches[1]).$($Matches[2]).$($Matches[3]).$revision"
Push-Location $projectRoot
try {
    & node --input-type=module -e 'import {readFileSync} from "node:fs";import {resolve} from "node:path";import {createHash} from "node:crypto";const root=process.argv[1];const m=JSON.parse(readFileSync(resolve(root,"bundle-manifest.json")));for(const [name,hash] of Object.entries(m.files)){if(name.includes("..")||createHash("sha256").update(readFileSync(resolve(root,name))).digest("hex")!==hash)throw Error("Bundle artifact mismatch: "+name);}' $bundlePath
    if($LASTEXITCODE){throw 'Bundle validation failed'}
    & $Iscc /Qp "/DBundleDir=$bundlePath" "/DReleaseVersion=$Version" "/DReleaseVersionNumeric=$numeric" installer/stream-dog-rust.iss
    if($LASTEXITCODE){throw 'Installer compilation failed'}
    $path=Join-Path $projectRoot "dist/Hellgato-$Version-Setup.exe"
    $hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($path))" | Set-Content -LiteralPath ($path+'.sha256') -Encoding ascii
    Write-Output $path
} finally {Pop-Location}
