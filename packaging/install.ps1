<#
.SYNOPSIS
    Installs (or updates) Ticket Assignment for everyone on this machine.

.DESCRIPTION
    Copies the PyInstaller build into Program Files and creates an
    all-users Start Menu shortcut, so people launch it like any other app
    and never have to see the _internal folder the .exe needs beside it.

    Machine-wide on purpose: a per-user install lands in one account's
    profile, which another account cannot read. That matters here because
    the app is often launched as a different account (via "run as
    different user") than the one signed in. Program Files is readable by
    every account, so the same install serves all of them.

    Requires administrator rights, and re-launches itself elevated if it
    wasn't started that way.

    Settings (database connection, dark mode, last location) live in each
    account's %APPDATA%\Ticket Assignment, not in the install directory,
    so they survive updates and stay per-account. A config.ini left in an
    older per-user install is migrated across automatically.

.PARAMETER SourcePath
    The built folder to install from. Defaults to whichever makes sense:
    this script's own folder when it sits next to Ticket Assignment.exe
    (that is, inside a copied build), otherwise the repo's
    dist\Ticket Assignment Windows produced by PyInstaller.

.PARAMETER Desktop
    Also create an all-users desktop shortcut.

.PARAMETER Uninstall
    Remove the installed copy and its shortcuts instead of installing.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Desktop
    .\install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string] $SourcePath,
    [switch] $Desktop,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'

$AppName      = 'Ticket Assignment'          # the .exe, shortcut and install folder
$BuildName    = 'Ticket Assignment Windows'  # what the spec names the build folder
$InstallDir   = Join-Path $env:ProgramFiles $AppName
$ExePath      = Join-Path $InstallDir "$AppName.exe"
$StartMenuDir = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
$StartShortcut= Join-Path $StartMenuDir "$AppName.lnk"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) "$AppName.lnk"

# Where a previous per-user install would have put things, so an upgrade
# can clean it up and carry its settings over.
$LegacyInstallDir = Join-Path (Join-Path $env:LOCALAPPDATA 'Programs') $AppName
$LegacyStartShortcut = Join-Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs') "$AppName.lnk"
$UserConfigDir = Join-Path $env:APPDATA $AppName

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-Admin {
    if (Test-Admin) { return }
    Write-Host 'Administrator rights are required to write to Program Files.'
    Write-Host 'Re-launching elevated...'
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($SourcePath) { $argList += @('-SourcePath', "`"$SourcePath`"") }
    if ($Desktop)    { $argList += '-Desktop' }
    if ($Uninstall)  { $argList += '-Uninstall' }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -Wait
    } catch {
        throw "Elevation was declined or failed. Re-run this script from an " +
              "administrator PowerShell prompt."
    }
    exit
}

function New-Shortcut([string] $LinkPath, [string] $Target, [string] $WorkDir) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($LinkPath)
    $lnk.TargetPath       = $Target
    $lnk.WorkingDirectory = $WorkDir
    $lnk.Description      = 'Round-robin ticket assignment'
    $lnk.Save()
}

function Stop-RunningApp {
    $running = Get-Process -Name $AppName -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "Closing running $AppName..."
        $running | Stop-Process -Force
        Start-Sleep -Milliseconds 500
    }
}

if ($Uninstall) {
    Assert-Admin
    Stop-RunningApp
    foreach ($lnk in @($StartShortcut, $DesktopShortcut, $LegacyStartShortcut)) {
        if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "Removed shortcut: $lnk" }
    }
    $removedSomething = $false
    foreach ($dir in @($InstallDir, $LegacyInstallDir)) {
        if (Test-Path $dir) {
            Remove-Item $dir -Recurse -Force
            Write-Host "Removed: $dir"
            $removedSomething = $true
        }
    }
    if (-not $removedSomething) { Write-Host "Nothing installed at $InstallDir" }
    # Settings live per-account in %APPDATA% and are deliberately left
    # alone, so reinstalling doesn't lose the database connection.
    if (Test-Path $UserConfigDir) {
        Write-Host "Left your settings in place: $UserConfigDir"
    }
    Write-Host "`nUninstalled."
    return
}

# ---- install / update ----------------------------------------------------

if (-not $SourcePath) {
    # This script ships inside the build folder as well as living in the
    # repo, so work out which situation we're in.
    if (Test-Path (Join-Path $PSScriptRoot "$AppName.exe")) {
        # Sitting next to the app: install from this folder.
        $SourcePath = $PSScriptRoot
    } else {
        # In the repo (packaging\): use the PyInstaller output.
        $SourcePath = Join-Path (Split-Path $PSScriptRoot -Parent) "dist\$BuildName"
    }
}
if (-not (Test-Path $SourcePath)) {
    throw "Build not found at '$SourcePath'. Run this first:`n" +
          "    python -m PyInstaller packaging\TicketAssignment_windows.spec"
}
$sourceExe = Join-Path $SourcePath "$AppName.exe"
if (-not (Test-Path $sourceExe)) {
    throw "'$SourcePath' doesn't look like a build -- no $AppName.exe inside it."
}

# Resolve now, before elevating: the elevated process gets a different
# %LOCALAPPDATA%, so a relative or profile-based source path would move.
$SourcePath = (Resolve-Path $SourcePath).Path

Assert-Admin
Stop-RunningApp

# Carry settings over from an older per-user install, which kept
# config.ini beside the .exe. The app looks in %APPDATA% now.
$legacyConfig = Join-Path $LegacyInstallDir 'config.ini'
$userConfig = Join-Path $UserConfigDir 'config.ini'
if ((Test-Path $legacyConfig) -and -not (Test-Path $userConfig)) {
    New-Item -ItemType Directory -Path $UserConfigDir -Force | Out-Null
    Copy-Item $legacyConfig $userConfig -Force
    Write-Host "Moved your settings to: $userConfig"
}

if (Test-Path $InstallDir) {
    Remove-Item $InstallDir -Recurse -Force
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Copy-Item (Join-Path $SourcePath '*') $InstallDir -Recurse -Force
# Never ship a config.ini into Program Files: it would be read-only for
# standard users, and settings are per-account in %APPDATA% now.
$strayConfig = Join-Path $InstallDir 'config.ini'
if (Test-Path $strayConfig) { Remove-Item $strayConfig -Force }
Write-Host "Installed to: $InstallDir"

# A previous per-user install would otherwise shadow this one in that
# account's Start Menu.
if (Test-Path $LegacyInstallDir) {
    Remove-Item $LegacyInstallDir -Recurse -Force
    Write-Host "Removed the older per-user install: $LegacyInstallDir"
}
if (Test-Path $LegacyStartShortcut) { Remove-Item $LegacyStartShortcut -Force }

New-Shortcut -LinkPath $StartShortcut -Target $ExePath -WorkDir $InstallDir
Write-Host "Start Menu shortcut created (all users)"
if ($Desktop) {
    New-Shortcut -LinkPath $DesktopShortcut -Target $ExePath -WorkDir $InstallDir
    Write-Host "Desktop shortcut created (all users)"
}

Write-Host "`nDone. Launch '$AppName' from the Start Menu."
Write-Host "Any account on this machine can run it; each keeps its own"
Write-Host "settings in %APPDATA%\$AppName."
Write-Host "To remove it later:  .\install.ps1 -Uninstall"
