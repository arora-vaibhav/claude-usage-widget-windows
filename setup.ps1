# Claude Usage Widget for Windows - Setup Script
# Run with: powershell -ExecutionPolicy Bypass -File setup.ps1

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

# ── Paths ──────────────────────────────────────────────────────────────────
$uvToolsRoot  = "$env:APPDATA\uv\tools\claude-usage-widget"
$sitePackages = "$uvToolsRoot\Lib\site-packages\claude_usage"
$claudeExe    = "$env:USERPROFILE\.local\bin\claude-usage.exe"
$launcherDst  = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\claude-usage-launcher.vbs"
$shortcutDst  = "$env:USERPROFILE\Desktop\Claude Usage.lnk"
$repoDir      = $PSScriptRoot

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN: $msg" -ForegroundColor Yellow }

# ── Uninstall ──────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Step "Uninstalling..."
    if (Test-Path $launcherDst) { Remove-Item $launcherDst; Write-OK "Removed launcher VBS" }
    if (Test-Path $shortcutDst) { Remove-Item $shortcutDst; Write-OK "Removed desktop shortcut" }
    uv tool uninstall claude-usage-widget 2>$null
    Write-OK "Uninstalled claude-usage-widget"
    Write-Host "`nDone." -ForegroundColor Green
    exit 0
}

# ── Prerequisites ──────────────────────────────────────────────────────────
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

# ── Install base package ───────────────────────────────────────────────────
Write-Step "Installing claude-usage-widget..."
uv tool install claude-usage-widget --force
Write-OK "Installed"

# Verify site-packages location
if (-not (Test-Path $sitePackages)) {
    Write-Host "ERROR: Could not find site-packages at: $sitePackages" -ForegroundColor Red
    Write-Host "Run 'uv tool install claude-usage-widget' manually and check the output path." -ForegroundColor Red
    exit 1
}

# ── Apply Windows patches ──────────────────────────────────────────────────
Write-Step "Applying Windows compatibility patches..."
$patchSrc = Join-Path $repoDir "patches"
foreach ($file in @("collector.py", "overlay.py", "widget.py")) {
    $src = Join-Path $patchSrc $file
    $dst = Join-Path $sitePackages $file
    if (Test-Path $src) {
        Copy-Item $src $dst -Force
        Write-OK "Patched $file"
    } else {
        Write-Warn "Patch file not found: $src"
    }
}

# ── Install console-less launcher ─────────────────────────────────────────
Write-Step "Installing launcher (no console window)..."
$vbsSrc = Join-Path $repoDir "launcher\claude-usage-launcher.vbs"
Copy-Item $vbsSrc $launcherDst -Force
Write-OK "Launcher installed to Start Menu"

# ── Create desktop shortcut ────────────────────────────────────────────────
Write-Step "Creating desktop shortcut..."
$shell = New-Object -ComObject WScript.Shell
$lnk   = $shell.CreateShortcut($shortcutDst)
$lnk.TargetPath  = "wscript.exe"
$lnk.Arguments   = "`"$launcherDst`""
$lnk.WindowStyle = 7
$lnk.Description = "Claude Usage Widget"
$lnk.Save()
Write-OK "Desktop shortcut created: Claude Usage.lnk"

# ── Done ───────────────────────────────────────────────────────────────────
Write-Host @"

============================================================
  Setup complete!

  - Double-click "Claude Usage" on your Desktop to start.
  - Right-click the widget to access settings or quit.
  - Drag the widget to reposition; position is remembered.
  - Click the widget to open the details panel.

  First run: make sure you have authenticated Claude CLI
  at least once ('claude' in a terminal) so credentials
  exist at ~/.claude/.credentials.json.
============================================================
"@ -ForegroundColor Green
