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
$launcherDst  = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\claude-usage-launcher.vbs"
$shortcutDst  = "$env:USERPROFILE\Desktop\Claude Usage.lnk"
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
    if (Test-Path $launcherDst) { Remove-Item $launcherDst; Write-OK "Removed launcher VBS" }
    if (Test-Path $shortcutDst) { Remove-Item $shortcutDst; Write-OK "Removed desktop shortcut" }
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

# -- Install console-less launcher -----------------------------------------
Write-Step "Installing launcher (no console window)..."
$vbsSrc = Join-Path $repoDir "launcher\claude-usage-launcher.vbs"
Copy-Item $vbsSrc $launcherDst -Force
Write-OK "Launcher installed to Start Menu"

# -- Create desktop shortcut ------------------------------------------------
Write-Step "Creating desktop shortcut..."
$shell = New-Object -ComObject WScript.Shell
$lnk   = $shell.CreateShortcut($shortcutDst)
$lnk.TargetPath  = "wscript.exe"
$lnk.Arguments   = "`"$launcherDst`""
$lnk.WindowStyle = 7
$lnk.Description = "Claude Usage Widget"
$lnk.Save()
Write-OK "Desktop shortcut created: Claude Usage.lnk"

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
