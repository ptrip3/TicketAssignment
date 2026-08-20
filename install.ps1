<#
.SYNOPSIS
    Installs (or updates) Ticket Assignment for the current user.

.DESCRIPTION
    Copies the PyInstaller build into the user's local app data and creates
    a Start Menu shortcut, so people launch it like any other app and never
    have to see the _internal folder the .exe needs beside it.

    Per-user by design: everything goes under %LOCALAPPDATA% and the
    per-user Start Menu, so no administrator rights are needed.

    An existing config.ini is preserved across updates -- that's where the
    database connection details live, so reinstalling must not reset it.

.PARAMETER SourcePath
    The built folder to install from. Defaults to "dist\Ticket Assignment"
    beside this script, i.e. what PyInstaller just produced.

.PARAMETER Desktop
    Also create a desktop shortcut.

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

$AppName      = 'Ticket Assignment'
$InstallRoot  = Join-Path $env:LOCALAPPDATA 'Programs'
$InstallDir   = Join-Path $InstallRoot $AppName
$ExePath      = Join-Path $InstallDir "$AppName.exe"
$StartMenuDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$StartShortcut= Join-Path $StartMenuDir "$AppName.lnk"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk"

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
    Stop-RunningApp
    foreach ($lnk in @($StartShortcut, $DesktopShortcut)) {
        if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "Removed shortcut: $lnk" }
    }
    if (Test-Path $InstallDir) {
        $keep = Join-Path $InstallDir 'config.ini'
        if (Test-Path $keep) {
            $backup = Join-Path $env:LOCALAPPDATA "$AppName-config-backup.ini"
            Copy-Item $keep $backup -Force
            Write-Host "Saved your settings to: $backup"
        }
        Remove-Item $InstallDir -Recurse -Force
        Write-Host "Removed: $InstallDir"
    } else {
        Write-Host "Nothing installed at $InstallDir"
    }
    Write-Host "`nUninstalled."
    return
}

# ---- install / update ----------------------------------------------------

if (-not $SourcePath) {
    $SourcePath = Join-Path $PSScriptRoot "dist\$AppName"
}
if (-not (Test-Path $SourcePath)) {
    throw "Build not found at '$SourcePath'. Run this first:`n" +
          "    python -m PyInstaller TicketAssignment_windows.spec"
}
$sourceExe = Join-Path $SourcePath "$AppName.exe"
if (-not (Test-Path $sourceExe)) {
    throw "'$SourcePath' doesn't look like a build -- no $AppName.exe inside it."
}

Stop-RunningApp

# Preserve existing settings (database connection, dark mode, last location)
$existingConfig = Join-Path $InstallDir 'config.ini'
$savedConfig = $null
if (Test-Path $existingConfig) {
    $savedConfig = Join-Path $env:TEMP "$AppName-config-$(Get-Random).ini"
    Copy-Item $existingConfig $savedConfig -Force
    Write-Host 'Preserving existing config.ini'
}

if (Test-Path $InstallDir) {
    Remove-Item $InstallDir -Recurse -Force
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Copy-Item (Join-Path $SourcePath '*') $InstallDir -Recurse -Force
Write-Host "Installed to: $InstallDir"

if ($savedConfig) {
    Copy-Item $savedConfig $existingConfig -Force
    Remove-Item $savedConfig -Force
    Write-Host 'Restored your existing settings'
}

New-Shortcut -LinkPath $StartShortcut -Target $ExePath -WorkDir $InstallDir
Write-Host "Start Menu shortcut created"
if ($Desktop) {
    New-Shortcut -LinkPath $DesktopShortcut -Target $ExePath -WorkDir $InstallDir
    Write-Host "Desktop shortcut created"
}

Write-Host "`nDone. Launch '$AppName' from the Start Menu."
Write-Host "To remove it later:  .\install.ps1 -Uninstall"
