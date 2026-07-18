[CmdletBinding()]
param(
    [ValidateSet('generic', 'kimi', 'all')]
    [string]$HostName = 'generic',

    [ValidateSet('user', 'project')]
    [string]$Scope = 'user',

    [string]$ProjectDir = (Get-Location).Path,

    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

& $Python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ is required'"

$DataHome = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
$InstallRoot = Join-Path $DataHome 'venice-media-skill'
$Venv = Join-Path $InstallRoot 'venv'
$BinDir = Join-Path $HOME '.local\bin'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvCommand = Join-Path $Venv 'Scripts\venice-media.exe'

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir | Out-Null
if (-not (Test-Path $VenvPython)) {
    & $Python -m venv $Venv
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --upgrade $Root

$Launcher = Join-Path $BinDir 'venice-media.cmd'
@"
@echo off
"$VenvCommand" %*
"@ | Set-Content -Encoding Ascii $Launcher

function Copy-Skill([string]$Destination) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Destination -Parent) | Out-Null
    $Staging = Join-Path (Split-Path $Destination -Parent) ('.venice-media.' + [guid]::NewGuid().ToString('N'))
    $Backup = "$Destination.rollback"
    Copy-Item -Recurse -Force (Join-Path $Root 'skills\venice-media') $Staging
    if (-not (Test-Path (Join-Path $Staging 'SKILL.md'))) { throw 'Bundled skill is missing SKILL.md' }
    if (Test-Path $Backup) { Remove-Item -Recurse -Force $Backup }
    if (Test-Path $Destination) { Move-Item $Destination $Backup }
    try {
        Move-Item $Staging $Destination
        if (Test-Path $Backup) { Remove-Item -Recurse -Force $Backup }
    } catch {
        if ((-not (Test-Path $Destination)) -and (Test-Path $Backup)) { Move-Item $Backup $Destination }
        throw
    }
}

if ($Scope -eq 'user') {
    if ($HostName -in @('generic', 'all')) { Copy-Skill (Join-Path $HOME '.agents\skills\venice-media') }
    if ($HostName -in @('kimi', 'all')) {
        $KimiHome = if ($env:KIMI_CODE_HOME) { $env:KIMI_CODE_HOME } else { Join-Path $HOME '.kimi-code' }
        Copy-Skill (Join-Path $KimiHome 'skills\venice-media')
    }
} else {
    $ResolvedProject = (Resolve-Path $ProjectDir).Path
    if ($HostName -in @('generic', 'all')) { Copy-Skill (Join-Path $ResolvedProject '.agents\skills\venice-media') }
    if ($HostName -in @('kimi', 'all')) {
        Copy-Skill (Join-Path $ResolvedProject '.kimi-code\skills\venice-media')
    }
}

Write-Host "Installed Venice Media Skill."
Write-Host "Executable launcher: $Launcher"
Write-Host "Add $BinDir to PATH, export VENICE_API_KEY, then run: venice-media doctor --online"
