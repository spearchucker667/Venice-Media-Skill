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
$Root = if ($Scope -eq 'project') { (Resolve-Path $ProjectDir).Path } else { $HOME }
$Targets = @()
if ($HostName -in @('generic', 'all')) {
    $Targets += if ($Scope -eq 'project') { Join-Path $Root '.agents\skills\venice-media' } else { Join-Path $HOME '.agents\skills\venice-media' }
}
if ($HostName -in @('kimi', 'all')) {
    $KimiRoot = if ($Scope -eq 'project') { Join-Path $Root '.kimi-code' } elseif ($env:KIMI_CODE_HOME) { $env:KIMI_CODE_HOME } else { Join-Path $HOME '.kimi-code' }
    $Targets += Join-Path $KimiRoot 'skills\venice-media'
}
foreach ($Target in $Targets) {
    if ((Test-Path $Target) -and $PSCmdlet.ShouldProcess($Target, 'Remove Venice Media Skill')) {
        Remove-Item -Recurse -Force $Target
    }
}
if ($RemoveBridge) {
    $DataHome = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
    $Bridge = Join-Path $DataHome 'venice-media-skill'
    $Launcher = Join-Path $HOME '.local\bin\venice-media.cmd'
    foreach ($Target in @($Bridge, $Launcher)) {
        if ((Test-Path $Target) -and $PSCmdlet.ShouldProcess($Target, 'Remove Venice Media bridge component')) {
            Remove-Item -Recurse -Force $Target
        }
    }
}
