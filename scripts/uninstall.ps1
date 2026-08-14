[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('generic', 'kimi', 'all')]
    [string]$HostName = 'all',
    [ValidateSet('user', 'project')]
    [string]$Scope = 'user',
    [string]$ProjectDir = (Get-Location).Path,
    [switch]$RemoveBridge
)

$ErrorActionPreference = 'Stop'

function Assert-NoReparsePoint([string]$PathToCheck) {
    $Current = [System.IO.Path]::GetFullPath($PathToCheck)
    while ($Current) {
        if (Test-Path -LiteralPath $Current) {
            $Item = Get-Item -LiteralPath $Current -Force
            if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to remove through reparse point: $Current"
            }
        }
        $Parent = [System.IO.Directory]::GetParent($Current)
        if ($null -eq $Parent) { break }
        $Current = $Parent.FullName
    }
}

function Remove-SafeDirectory([string]$Target, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Target)) { return }
    Assert-NoReparsePoint $Target
    if (-not (Test-Path -LiteralPath $Target -PathType Container)) {
        throw "Refusing to remove non-directory $Description target: $Target"
    }
    if ($PSCmdlet.ShouldProcess($Target, "Remove $Description")) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
}

function Remove-SafeFile([string]$Target, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Target)) { return }
    Assert-NoReparsePoint $Target
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) {
        throw "Refusing to remove non-file $Description target: $Target"
    }
    if ($PSCmdlet.ShouldProcess($Target, "Remove $Description")) {
        Remove-Item -LiteralPath $Target -Force
    }
}

$Root = if ($Scope -eq 'project') {
    $Resolved = (Resolve-Path $ProjectDir).Path
    Assert-NoReparsePoint $Resolved
    $Resolved
} else {
    $HOME
}

$Targets = @()
if ($HostName -in @('generic', 'all')) {
    $Targets += if ($Scope -eq 'project') {
        Join-Path $Root '.agents\skills\venice-media'
    } else {
        Join-Path $HOME '.agents\skills\venice-media'
    }
}
if ($HostName -in @('kimi', 'all')) {
    $KimiRoot = if ($Scope -eq 'project') {
        Join-Path $Root '.kimi-code'
    } elseif ($env:KIMI_CODE_HOME) {
        $env:KIMI_CODE_HOME
    } else {
        Join-Path $HOME '.kimi-code'
    }
    $Targets += Join-Path $KimiRoot 'skills\venice-media'
}

foreach ($Target in $Targets) {
    Remove-SafeDirectory $Target 'Venice Media Skill'
}

if ($RemoveBridge) {
    $DataHome = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
    Remove-SafeDirectory (Join-Path $DataHome 'venice-media-skill') 'Venice Media bridge'
    Remove-SafeFile (Join-Path $HOME '.local\bin\venice-media.cmd') 'Venice Media launcher'
}
