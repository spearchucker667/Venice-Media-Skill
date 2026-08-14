[CmdletBinding()]
param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$Installer = Join-Path $PSScriptRoot 'install.ps1'
$Uninstaller = Join-Path $PSScriptRoot 'uninstall.ps1'
$TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("venice-media-install-test-" + [guid]::NewGuid().ToString('N'))
$TestHome = Join-Path $TestRoot 'home'
$env:HOME = $TestHome
$env:USERPROFILE = $TestHome
$env:LOCALAPPDATA = Join-Path $TestRoot 'local-app-data'
$env:KIMI_CODE_HOME = Join-Path $TestHome '.kimi-code'

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "assertion failed: $Message" }
}

function Invoke-Script([string]$Script, [string[]]$Arguments, [bool]$ShouldSucceed = $true) {
    & pwsh -NoLogo -NoProfile -File $Script @Arguments
    $Code = $LASTEXITCODE
    if ($ShouldSucceed -and $Code -ne 0) { throw "$Script failed with exit code $Code" }
    if (-not $ShouldSucceed -and $Code -eq 0) { throw "$Script unexpectedly succeeded" }
}

function Assert-NoRecoveryArtifacts([string]$Parent) {
    $Leftovers = @(Get-ChildItem -LiteralPath $Parent -Force | Where-Object {
        $_.Name -like '.venice-media.rollback-*' -or $_.Name -like '.venice-media.staging.*'
    })
    Assert-True ($Leftovers.Count -eq 0) "unexpected recovery artifacts under $Parent"
}

try {
    New-Item -ItemType Directory -Force -Path $TestHome | Out-Null

    Invoke-Script $Installer @('-HostName', 'generic', '-Scope', 'user', '-Python', $Python)
    Assert-True (Test-Path -LiteralPath (Join-Path $TestHome '.agents\skills\venice-media\SKILL.md') -PathType Leaf) 'user install missing SKILL.md'
    Assert-True (Test-Path -LiteralPath (Join-Path $TestHome '.local\bin\venice-media.cmd') -PathType Leaf) 'launcher missing'

    $Project = Join-Path $TestRoot 'project'
    New-Item -ItemType Directory -Path $Project | Out-Null
    Invoke-Script $Installer @('-HostName', 'all', '-Scope', 'project', '-ProjectDir', $Project, '-Python', $Python)
    Invoke-Script $Installer @('-HostName', 'all', '-Scope', 'project', '-ProjectDir', $Project, '-Python', $Python)
    Assert-True (Test-Path -LiteralPath (Join-Path $Project '.agents\skills\venice-media\SKILL.md') -PathType Leaf) 'generic project install missing'
    Assert-True (Test-Path -LiteralPath (Join-Path $Project '.kimi-code\skills\venice-media\SKILL.md') -PathType Leaf) 'Kimi project install missing'
    Assert-NoRecoveryArtifacts (Join-Path $Project '.agents\skills')
    Assert-NoRecoveryArtifacts (Join-Path $Project '.kimi-code\skills')

    $FileProject = Join-Path $TestRoot 'regular-file-project'
    $FileDestination = Join-Path $FileProject '.agents\skills\venice-media'
    New-Item -ItemType Directory -Force -Path (Split-Path $FileDestination -Parent) | Out-Null
    Set-Content -LiteralPath $FileDestination -Value 'preserve-me' -NoNewline
    Invoke-Script $Installer @('-HostName', 'generic', '-Scope', 'project', '-ProjectDir', $FileProject, '-Python', $Python) $false
    Assert-True ((Get-Content -LiteralPath $FileDestination -Raw) -eq 'preserve-me') 'regular-file destination was modified'

    $OrphanProject = Join-Path $TestRoot 'orphan-project'
    $OrphanParent = Join-Path $OrphanProject '.agents\skills'
    $Orphan = Join-Path $OrphanParent '.venice-media.rollback-20260101T000000Z-deadbeef'
    New-Item -ItemType Directory -Force -Path $Orphan | Out-Null
    Set-Content -LiteralPath (Join-Path $Orphan 'SKILL.md') -Value 'recover-me'
    Set-Content -LiteralPath "$Orphan.metadata.json" -Value '{"schema":"vms-backup-v1"}'
    Invoke-Script $Installer @('-HostName', 'generic', '-Scope', 'project', '-ProjectDir', $OrphanProject, '-Python', $Python) $false
    Assert-True (Test-Path -LiteralPath (Join-Path $Orphan 'SKILL.md') -PathType Leaf) 'orphan backup was removed'
    Assert-True (Test-Path -LiteralPath "$Orphan.metadata.json" -PathType Leaf) 'orphan metadata was removed'

    $DestinationLinkProject = Join-Path $TestRoot 'destination-link-project'
    $DestinationLinkParent = Join-Path $DestinationLinkProject '.agents\skills'
    $DestinationLinkTarget = Join-Path $TestRoot 'destination-link-target'
    New-Item -ItemType Directory -Force -Path $DestinationLinkParent, $DestinationLinkTarget | Out-Null
    New-Item -ItemType Junction -Path (Join-Path $DestinationLinkParent 'venice-media') -Target $DestinationLinkTarget | Out-Null
    Invoke-Script $Installer @('-HostName', 'generic', '-Scope', 'project', '-ProjectDir', $DestinationLinkProject, '-Python', $Python) $false

    $ParentLinkProject = Join-Path $TestRoot 'parent-link-project'
    $ParentLinkTarget = Join-Path $TestRoot 'parent-link-target'
    New-Item -ItemType Directory -Force -Path $ParentLinkProject, $ParentLinkTarget | Out-Null
    New-Item -ItemType Junction -Path (Join-Path $ParentLinkProject '.agents') -Target $ParentLinkTarget | Out-Null
    Invoke-Script $Installer @('-HostName', 'generic', '-Scope', 'project', '-ProjectDir', $ParentLinkProject, '-Python', $Python) $false

    Invoke-Script $Uninstaller @('-HostName', 'all', '-Scope', 'project', '-ProjectDir', $Project)
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Project '.agents\skills\venice-media'))) 'generic uninstall failed'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Project '.kimi-code\skills\venice-media'))) 'Kimi uninstall failed'

    Invoke-Script $Uninstaller @('-HostName', 'generic', '-Scope', 'project', '-ProjectDir', $DestinationLinkProject) $false
    Assert-True (Test-Path -LiteralPath $DestinationLinkTarget -PathType Container) 'uninstall touched reparse target'

    Write-Host 'test-install.ps1: all Windows installer safety checks passed'
} finally {
    if (Test-Path -LiteralPath $TestRoot) { Remove-Item -LiteralPath $TestRoot -Recurse -Force }
}

exit 0
