# Claude Usage Widget for Windows - Setup Script (full-source fork)
# Run with: powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Installs the app from this repository's source (src/claude_usage) with
# `uv tool install`; only its dependencies come from PyPI.

param(
    [switch]$Uninstall,
    [switch]$Autostart   # also launch the widget automatically at login
)

$ErrorActionPreference = "Stop"

# -- Paths ------------------------------------------------------------------
$repoDir      = $PSScriptRoot
$appDir       = "$env:LOCALAPPDATA\claude-usage"   # also where the app keeps its store
$launcherDst  = Join-Path $appDir "launcher.vbs"
$iconDst      = Join-Path $appDir "claude-usage.ico"
$startMenuDst = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Claude Usage.lnk"
$shortcutDst  = "$env:USERPROFILE\Desktop\Claude Usage.lnk"
# Written by older versions of this script: a launcher script listed in the Start menu, and a shared
# Start menu shortcut to it with the script host's icon.
$legacyLauncher = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\claude-usage-launcher.vbs"
$legacyShared   = "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Claude Usage.lnk"
$startupDir   = [Environment]::GetFolderPath('Startup')
# The same file the app's "Launch at startup" setting creates and removes.
$autostartDst = Join-Path $startupDir "claude-usage-widget.vbs"
$legacyAutostart = Join-Path $startupDir "Claude Usage.lnk"   # written by older versions of this script

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN: $msg" -ForegroundColor Yellow }

# -- Uninstall --------------------------------------------------------------
if ($Uninstall) {
    Write-Step "Uninstalling..."
    foreach ($p in @($launcherDst, $iconDst, $legacyLauncher)) {
        if (Test-Path $p) { Remove-Item $p; Write-OK "Removed $(Split-Path $p -Leaf)" }
    }
    foreach ($p in @($startMenuDst, $shortcutDst, $legacyShared)) {
        if (Test-Path $p) { Remove-Item $p -ErrorAction SilentlyContinue; Write-OK "Removed shortcut $p" }
    }
    foreach ($p in @($autostartDst, $legacyAutostart)) {
        if (Test-Path $p) { Remove-Item $p; Write-OK "Removed autostart entry" }
    }
    uv tool uninstall claude-usage-widget 2>$null
    Write-OK "Uninstalled claude-usage-widget"
    Write-Host "`nDone." -ForegroundColor Green
    exit 0
}

# -- Prerequisites ----------------------------------------------------------
Write-Step "Checking prerequisites..."

# uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Step "Installing uv package manager..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:PATH += ";$env:USERPROFILE\.local\bin"
}
Write-OK "uv found: $(uv --version)"

# Claude CLI (needed for auth)
if (-not (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) {
    Write-Warn "Claude CLI not found at ~/.local/bin/claude.exe"
    Write-Host "   Install it from: https://docs.anthropic.com/en/docs/claude-code" -ForegroundColor Yellow
    Write-Host "   Then run 'claude' once to authenticate before starting the widget." -ForegroundColor Yellow
}

# -- Install from local source ----------------------------------------------
Write-Step "Installing claude-usage-widget from local source..."
# uv caches the built package by pyproject.toml's mtime; without the reinstall an update that
# changes only .py or QML files would silently reinstall the old build.
uv tool install $repoDir --force --reinstall-package claude-usage-widget
Write-OK "Installed from $repoDir"

# -- Install console-less launcher and the icon ----------------------------
Write-Step "Installing launcher (no console window) and icon..."
New-Item -ItemType Directory -Force $appDir | Out-Null
$vbsSrc = Join-Path $repoDir "launcher\claude-usage-launcher.vbs"
Copy-Item $vbsSrc $launcherDst -Force
Copy-Item (Join-Path $repoDir "src\claude_usage\icons\claude-usage.ico") $iconDst -Force
Write-OK "Launcher and icon installed to $appDir"

# -- Start menu and desktop shortcuts ---------------------------------------
Write-Step "Creating Start menu and desktop shortcuts..."
$shell = New-Object -ComObject WScript.Shell
foreach ($dst in @($startMenuDst, $shortcutDst)) {
    $lnk = $shell.CreateShortcut($dst)
    $lnk.TargetPath   = "wscript.exe"
    $lnk.Arguments    = "`"$launcherDst`""
    $lnk.WindowStyle  = 7
    $lnk.IconLocation = "$iconDst,0"
    $lnk.Description  = "Claude Usage"
    $lnk.Save()
}
if (Test-Path $legacyLauncher) { Remove-Item $legacyLauncher }
if (Test-Path $legacyShared) {
    # Only an admin can edit it, but its owner can delete it; the new per-user shortcut replaces it.
    Remove-Item $legacyShared -ErrorAction SilentlyContinue
    if (Test-Path $legacyShared) { Write-Warn "Could not remove the old shared shortcut $legacyShared (it has the old icon). Delete it as an administrator." }
}
Write-OK "Shortcuts created: Start menu and Desktop (Claude Usage)"

# -- Optional: launch automatically at login --------------------------------
# An entry from an older version of this script counts as a yes, so the Settings toggle sees it.
if ($Autostart -or (Test-Path $legacyAutostart)) {
    Write-Step "Enabling autostart at login..."
    Copy-Item $vbsSrc $autostartDst -Force
    if (Test-Path $legacyAutostart) { Remove-Item $legacyAutostart }
    Write-OK "Autostart enabled. Turn it off in Settings or with: setup.ps1 -Uninstall"
} else {
    Write-Host "   (Tip: turn on Launch at startup in Settings, or re-run with -Autostart.)" -ForegroundColor DarkGray
}

# -- Done --------------------------------------------------------------------
Write-Host @"

============================================================
  Setup complete!

  - Double-click "Claude Usage" on your Desktop to start.
  - Click the overlay to open the app; right-click it for the menu.
  - Drag the overlay to move it; the position is remembered.
  - The first start reads all your transcripts, which can
    take 10 to 40 minutes. The app shows the progress.

  First run: sign in to Claude Code at least once ('claude'
  in a terminal) so its credentials exist in ~/.claude.
============================================================
"@ -ForegroundColor Green
