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
if ($LASTEXITCODE -ne 0) { throw "Python validation failed with exit code $LASTEXITCODE" }

$DataHome = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
$InstallRoot = Join-Path $DataHome 'venice-media-skill'
$Venv = Join-Path $InstallRoot 'venv'
$BinDir = Join-Path $HOME '.local\bin'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvCommand = Join-Path $Venv 'Scripts\venice-media.exe'

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir | Out-Null
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed with exit code $LASTEXITCODE" }
}
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
& $VenvPython -m pip install --upgrade $Root
if ($LASTEXITCODE -ne 0) { throw "Package installation failed with exit code $LASTEXITCODE" }

$Launcher = Join-Path $BinDir 'venice-media.cmd'
$LauncherStaging = "$Launcher.staging.$PID.$([guid]::NewGuid().ToString('N'))"
try {
    [System.IO.File]::WriteAllText(
        $LauncherStaging,
        "@echo off`r`n`"$VenvCommand`" %*`r`n",
        [System.Text.Encoding]::ASCII
    )
    Move-Item -LiteralPath $LauncherStaging -Destination $Launcher -Force
} finally {
    if (Test-Path -LiteralPath $LauncherStaging) { Remove-Item -LiteralPath $LauncherStaging -Force }
}

function Assert-NoReparsePoint([string]$PathToCheck) {
    $Current = [System.IO.Path]::GetFullPath($PathToCheck)
    while ($Current) {
        if (Test-Path -LiteralPath $Current) {
            $Item = Get-Item -LiteralPath $Current -Force
            if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to install through reparse point: $Current"
            }
        }
        $Parent = [System.IO.Directory]::GetParent($Current)
        if ($null -eq $Parent) { break }
        $Current = $Parent.FullName
    }
}

function Assert-SafeDestination([string]$Destination) {
    Assert-NoReparsePoint $Destination
    if ((Test-Path -LiteralPath $Destination) -and -not (Test-Path -LiteralPath $Destination -PathType Container)) {
        throw "Skill destination is not a directory, refusing to clobber: $Destination"
    }
}

function Assert-NoOrphanBackup([string]$Destination) {
    $Parent = Split-Path $Destination -Parent
    if (-not (Test-Path -LiteralPath $Parent -PathType Container)) { return }
    $Prefix = ".$(Split-Path $Destination -Leaf).rollback-"
    $Orphans = @(Get-ChildItem -LiteralPath $Parent -Force | Where-Object { $_.Name.StartsWith($Prefix) })
    if ($Orphans.Count -gt 0) {
        $List = ($Orphans | ForEach-Object { "  - $($_.FullName)" }) -join [Environment]::NewLine
        throw "Refusing to install: previous install left an unrecovered backup.`nInspect and recover or remove it, then retry:`n$List"
    }
}

function Copy-Skill([string]$Destination) {
    Assert-SafeDestination $Destination
    Assert-NoOrphanBackup $Destination
    $Parent = Split-Path $Destination -Parent
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    Assert-NoReparsePoint $Parent

    $Leaf = Split-Path $Destination -Leaf
    $Staging = Join-Path $Parent (".$Leaf.staging." + [guid]::NewGuid().ToString('N'))
    $Timestamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $Backup = Join-Path $Parent (".$Leaf.rollback-$Timestamp-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $Metadata = "$Backup.metadata.json"
    $BackupCreated = $false

    try {
        New-Item -ItemType Directory -Path $Staging | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $Root 'skills\venice-media') -Force |
            Copy-Item -Destination $Staging -Recurse -Force
        if (-not (Test-Path -LiteralPath (Join-Path $Staging 'SKILL.md') -PathType Leaf)) {
            throw 'Bundled skill is missing SKILL.md'
        }

        if (Test-Path -LiteralPath $Destination) {
            $Payload = @{
                schema = 'vms-backup-v1'
                destination = $Destination
                created_at = [DateTime]::UtcNow.ToString('o')
                pid = $PID
            } | ConvertTo-Json -Compress
            [System.IO.File]::WriteAllText($Metadata, $Payload, [System.Text.UTF8Encoding]::new($false))
            Move-Item -LiteralPath $Destination -Destination $Backup
            $BackupCreated = $true
        }

        Move-Item -LiteralPath $Staging -Destination $Destination
        if ($BackupCreated) {
            Remove-Item -LiteralPath $Backup -Recurse -Force
            Remove-Item -LiteralPath $Metadata -Force
            $BackupCreated = $false
        }
    } catch {
        $InstallError = $_
        if ($BackupCreated -and (Test-Path -LiteralPath $Backup)) {
            try {
                if (Test-Path -LiteralPath $Destination) {
                    Assert-NoReparsePoint $Destination
                    Remove-Item -LiteralPath $Destination -Recurse -Force
                }
                Move-Item -LiteralPath $Backup -Destination $Destination
                Remove-Item -LiteralPath $Metadata -Force -ErrorAction SilentlyContinue
                $BackupCreated = $false
            } catch {
                throw "Install failed and recovery also failed. Backup preserved at $Backup with metadata $Metadata. Install error: $InstallError Recovery error: $_"
            }
        }
        throw $InstallError
    } finally {
        if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
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
    Assert-NoReparsePoint $ResolvedProject
    if ($HostName -in @('generic', 'all')) { Copy-Skill (Join-Path $ResolvedProject '.agents\skills\venice-media') }
    if ($HostName -in @('kimi', 'all')) {
        Copy-Skill (Join-Path $ResolvedProject '.kimi-code\skills\venice-media')
    }
}

Write-Host "Installed Venice Media Skill."
Write-Host "Executable launcher: $Launcher"
Write-Host "Add $BinDir to PATH, export VENICE_API_KEY, then run: venice-media doctor --online"
