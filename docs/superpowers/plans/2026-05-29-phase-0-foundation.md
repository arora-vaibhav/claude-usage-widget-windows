# Phase 0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, chosen) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the patch-overlay distribution into a maintainable full-source fork of `claude-usage-widget` under git, installable via `uv tool install .`, with a test harness — behavior-identical to the current working install.

**Architecture:** Continue the existing public repo (`arora-vaibhav/claude-usage-widget-windows`) in place to preserve history/launcher/setup/README. Vendor the *currently installed* (already-patched) `claude_usage` source into a `src/` layout, add `pyproject.toml`/`LICENSE`/`NOTICE`/tests, and rewrite `setup.ps1` to install from local source instead of PyPI-plus-patches.

**Tech Stack:** Python ≥3.10, PySide6-Essentials, setuptools build backend, uv tool install, pytest.

**Source of truth for vendoring:** `C:\Users\V\AppData\Roaming\uv\tools\claude-usage-widget\Lib\site-packages\claude_usage\` (the live, patched, working install).

**Risk control:** We vendor the *exact* working source, so installing from it reproduces the current app. We do not modify any `.py` module logic in Phase 0 (pure restructure + packaging).

---

### Task 1: Continue the existing repo in place (git)

**Files:**
- Create: `.git/` (via init), `.gitignore` (append)

- [ ] **Step 1: Initialize git in the working dir**

Run:
```
git -C "C:/Projects/Claude Widget" init
git -C "C:/Projects/Claude Widget" remote add origin https://github.com/arora-vaibhav/claude-usage-widget-windows.git
git -C "C:/Projects/Claude Widget" fetch origin
```
Expected: fetch downloads `main` with the existing port history.

- [ ] **Step 2: Check out `main` tracking origin/main (keeps untracked docs/ + .omc/)**

Run:
```
git -C "C:/Projects/Claude Widget" checkout -b main --track origin/main
git -C "C:/Projects/Claude Widget" log --oneline -5
git -C "C:/Projects/Claude Widget" status
```
Expected: working tree now contains `README.md`, `setup.ps1`, `launcher/`, `patches/`, `.gitignore`; `docs/` and `.omc/` show as untracked; `git log` shows the existing commit history (continuity preserved).

- [ ] **Step 3: Ignore local tooling state**

Append to `.gitignore`:
```
# Local agent/editor tooling state
.omc/
.pytest_cache/
*.egg-info/
```
(Existing `.gitignore` already covers `__pycache__/`, `dist/`, `build/`, `.venv/`, `*.lnk`, credentials.)

- [ ] **Step 4: Commit the design spec + gitignore (first foundation commit)**

Run:
```
git -C "C:/Projects/Claude Widget" add .gitignore docs/
git -C "C:/Projects/Claude Widget" commit -m "docs: add modernization design spec; ignore local tooling state"
```
Expected: commit succeeds on top of existing history.

---

### Task 2: Verify the live install matches the tracked patches (provenance)

**Files:** read-only (`patches/*.py` vs installed `claude_usage/*.py`)

- [ ] **Step 1: Diff each patched module against the live install**

Run (PowerShell):
```powershell
$inst = "$env:APPDATA\uv\tools\claude-usage-widget\Lib\site-packages\claude_usage"
foreach ($f in "collector.py","overlay.py","widget.py","cache_analyzer.py","ticker.py") {
  $a = Join-Path "C:\Projects\Claude Widget\patches" $f
  $b = Join-Path $inst $f
  $d = Compare-Object (Get-Content $a) (Get-Content $b)
  if ($d) { "DIFFERS: $f ($($d.Count) lines)" } else { "match: $f" }
}
```
Expected: ideally all "match". If any DIFFER, the live install diverged from tracked patches — record which, and treat the **live install as source of truth** (it is the working app). Note the result in the Task 3 commit message.

---

### Task 3: Vendor full source into `src/claude_usage/`

**Files:**
- Create: `src/claude_usage/**` (full tree)
- Delete: `patches/` (superseded by full source)

- [ ] **Step 1: Copy the live source into src/ (excluding bytecode)**

Run (PowerShell):
```powershell
$src = "$env:APPDATA\uv\tools\claude-usage-widget\Lib\site-packages\claude_usage"
$dst = "C:\Projects\Claude Widget\src\claude_usage"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
robocopy $src $dst /E /XD __pycache__ /XF *.pyc | Out-Null
# robocopy exit codes 0-7 are success; treat >=8 as failure
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" } else { "vendored OK" }
```

- [ ] **Step 2: Confirm the tree is complete and clean**

Run (PowerShell):
```powershell
$dst = "C:\Projects\Claude Widget\src\claude_usage"
"py modules: " + (Get-ChildItem $dst -Recurse -Filter *.py | Measure-Object).Count
"has icons/claude-tray.svg: " + (Test-Path "$dst\icons\claude-tray.svg")
"has skins/_adapter.py: " + (Test-Path "$dst\skins\_adapter.py")
"stray __pycache__: " + ((Get-ChildItem $dst -Recurse -Directory -Filter __pycache__ | Measure-Object).Count)
```
Expected: ~36 `.py` files, icons + skins present, 0 `__pycache__` dirs.

- [ ] **Step 3: Remove the now-redundant patches overlay**

Run:
```
git -C "C:/Projects/Claude Widget" rm -r patches
```
Expected: `patches/` staged for deletion (its content now lives, fully merged, in `src/claude_usage/`).

- [ ] **Step 4: Commit the vendored source**

Run:
```
git -C "C:/Projects/Claude Widget" add src/
git -C "C:/Projects/Claude Widget" commit -m "feat: vendor full v0.6.6 source into src/ (patches merged); drop patch overlay"
```

---

### Task 4: Create `pyproject.toml` (src layout, deps, entry point, build)

**Files:**
- Create: `C:\Projects\Claude Widget\pyproject.toml`

- [ ] **Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "claude-usage-widget"
dynamic = ["version"]
description = "Desktop widget and CLI that shows real-time Claude Code usage limits and cost (Windows-focused fork)."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [{ name = "Burak" }]
maintainers = [{ name = "Vaibhav Arora" }]
keywords = ["claude", "anthropic", "usage", "rate-limit", "widget"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Win32 (MS Windows)",
    "Environment :: X11 Applications :: Qt",
    "Environment :: MacOS X",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS :: MacOS X",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: System :: Monitoring",
]
dependencies = [
    "PySide6-Essentials>=6.5,<7",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.urls]
Homepage = "https://github.com/arora-vaibhav/claude-usage-widget-windows"
Upstream = "https://github.com/bozdemir/claude-usage-widget"
Issues = "https://github.com/arora-vaibhav/claude-usage-widget-windows/issues"

[project.scripts]
claude-usage = "claude_usage.cli:main"

[tool.setuptools]
include-package-data = true

[tool.setuptools.packages.find]
where = ["src"]
include = ["claude_usage*"]

[tool.setuptools.dynamic]
version = { attr = "claude_usage.__version__" }

[tool.setuptools.package-data]
claude_usage = ["py.typed", "icons/*.svg"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Sanity-check the build metadata resolves**

Run:
```
python -c "import tomllib,pathlib; d=tomllib.loads(pathlib.Path(r'C:/Projects/Claude Widget/pyproject.toml').read_text()); print(d['project']['name'], d['project']['scripts'])"
```
Expected: `claude-usage-widget {'claude-usage': 'claude_usage.cli:main'}`

---

### Task 5: Add LICENSE + NOTICE (attribution)

**Files:**
- Create: `C:\Projects\Claude Widget\LICENSE`
- Create: `C:\Projects\Claude Widget\NOTICE`

- [ ] **Step 1: Write LICENSE (upstream MIT, verbatim)**

```
MIT License

Copyright (c) 2026 Burak

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Write NOTICE (port attribution)**

```
claude-usage-widget-windows
===========================

This project is a Windows-focused fork of "claude-usage-widget" by Burak,
licensed under the MIT License (see LICENSE).

  Upstream: https://github.com/bozdemir/claude-usage-widget

Fork maintained by Vaibhav Arora (https://github.com/arora-vaibhav). Changes
include Windows compatibility (OAuth token refresh, memory-safe scans, BOM
handling, backoff/retry, exception logging), a full-source build, a usage
history store + dashboard, and UI modernization.
```

---

### Task 6: Test scaffold + characterization tests for pure modules

**Files:**
- Create: `tests/__init__.py` (empty), `tests/unit/__init__.py` (empty)
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_history.py`

> These cover the two pure modules read in full during design (`config.py`,
> `history.py`). They import no Qt, so they run headless. They lock in current
> behavior before later phases change anything.

- [ ] **Step 1: Write tests/unit/test_config.py**

```python
import json

from claude_usage.config import DEFAULT_CONFIG, load_config


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(str(tmp_path / "does-not-exist.json"))
    assert cfg == DEFAULT_CONFIG
    # Returned dict must be a copy, not the module-level default.
    assert cfg is not DEFAULT_CONFIG


def test_user_values_override_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"theme": "gruvbox-dark", "refresh_seconds": 5}))
    cfg = load_config(str(p))
    assert cfg["theme"] == "gruvbox-dark"
    assert cfg["refresh_seconds"] == 5
    # Untouched keys keep their defaults.
    assert cfg["weekly_token_limit"] == DEFAULT_CONFIG["weekly_token_limit"]


def test_bad_json_falls_back_to_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not valid json")
    cfg = load_config(str(p))
    assert cfg["theme"] == DEFAULT_CONFIG["theme"]
```

- [ ] **Step 2: Write tests/unit/test_history.py**

```python
from claude_usage.history import aggregate, append_sample, load_samples, prune


def test_append_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "h.jsonl")
    append_sample(path, ts=100.0, session_util=0.5, weekly_util=0.4)
    append_sample(path, ts=200.0, session_util=0.6, weekly_util=0.45)
    rows = load_samples(path)
    assert [r["ts"] for r in rows] == [100.0, 200.0]
    assert rows[1]["session"] == 0.6


def test_load_samples_since_filter(tmp_path):
    path = str(tmp_path / "h.jsonl")
    append_sample(path, 100.0, 0.1, 0.1)
    append_sample(path, 300.0, 0.2, 0.2)
    assert [r["ts"] for r in load_samples(path, since_ts=200.0)] == [300.0]


def test_aggregate_takes_max_per_bucket(tmp_path):
    # window 100s into 10 buckets (10s each), ending at now=1000 (start=900).
    pts = [
        {"ts": 905.0, "session": 0.3, "weekly": 0.1},
        {"ts": 908.0, "session": 0.7, "weekly": 0.1},   # same bucket 0 -> max 0.7
        {"ts": 995.0, "session": 0.4, "weekly": 0.2},   # bucket 9
    ]
    out = aggregate(pts, "session", now=1000.0, window_seconds=100.0, n_buckets=10)
    assert len(out) == 10
    assert out[0] == 0.7
    assert out[9] == 0.4
    assert out[5] == 0.0  # empty bucket


def test_prune_drops_old(tmp_path):
    path = str(tmp_path / "h.jsonl")
    append_sample(path, 100.0, 0.1, 0.1)
    append_sample(path, 1000.0, 0.2, 0.2)
    kept = prune(path, keep_seconds=500.0, now=1000.0)  # cutoff=500 -> drop ts=100
    assert kept == 1
    assert [r["ts"] for r in load_samples(path)] == [1000.0]
```

- [ ] **Step 3: Create the empty package markers**

Create empty files `tests/__init__.py` and `tests/unit/__init__.py`.

---

### Task 7: Rewrite `setup.ps1` to install from local source

**Files:**
- Modify (rewrite): `C:\Projects\Claude Widget\setup.ps1`

- [ ] **Step 1: Replace setup.ps1 with the local-source installer**

```powershell
# Claude Usage Widget for Windows - Setup Script (full-source fork)
# Run with: powershell -ExecutionPolicy Bypass -File setup.ps1
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$repoDir      = $PSScriptRoot
$launcherDst  = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\claude-usage-launcher.vbs"
$shortcutDst  = "$env:USERPROFILE\Desktop\Claude Usage.lnk"

function Write-Step($m){ Write-Host "`n>> $m" -ForegroundColor Cyan }
function Write-OK($m){ Write-Host "   OK: $m" -ForegroundColor Green }
function Write-Warn($m){ Write-Host "   WARN: $m" -ForegroundColor Yellow }

if ($Uninstall) {
    Write-Step "Uninstalling..."
    if (Test-Path $launcherDst) { Remove-Item $launcherDst; Write-OK "Removed launcher VBS" }
    if (Test-Path $shortcutDst) { Remove-Item $shortcutDst; Write-OK "Removed desktop shortcut" }
    uv tool uninstall claude-usage-widget 2>$null
    Write-OK "Uninstalled claude-usage-widget"
    exit 0
}

Write-Step "Checking prerequisites..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Step "Installing uv package manager..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:PATH += ";$env:USERPROFILE\.local\bin"
}
Write-OK "uv found: $(uv --version)"

if (-not (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) {
    Write-Warn "Claude CLI not found at ~/.local/bin/claude.exe"
    Write-Host "   Install from https://docs.anthropic.com/en/docs/claude-code and run 'claude' once to authenticate." -ForegroundColor Yellow
}

# Install the widget FROM THIS REPO'S SOURCE (no PyPI, no patch copying).
Write-Step "Installing claude-usage-widget from local source..."
uv tool install $repoDir --force
Write-OK "Installed from $repoDir"

Write-Step "Installing launcher (no console window)..."
Copy-Item (Join-Path $repoDir "launcher\claude-usage-launcher.vbs") $launcherDst -Force
Write-OK "Launcher installed to Start Menu"

Write-Step "Creating desktop shortcut..."
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcutDst)
$lnk.TargetPath = "wscript.exe"
$lnk.Arguments = "`"$launcherDst`""
$lnk.WindowStyle = 7
$lnk.Description = "Claude Usage Widget"
$lnk.Save()
Write-OK "Desktop shortcut created"

Write-Host "`nSetup complete. Double-click 'Claude Usage' on your Desktop to start." -ForegroundColor Green
```

---

### Task 8: Update README for the full-source workflow

**Files:**
- Modify: `C:\Projects\Claude Widget\README.md`

- [ ] **Step 1: Rewrite the install/dev sections**

Replace the "patches/setup" description with: full-source layout (`src/claude_usage/`),
one-click install via `setup.ps1` (now installs from source), manual install via
`uv tool install .`, dev install `uv tool install -e .` + `pytest`, and an attribution
line crediting upstream (Burak, MIT). Keep the existing feature list + screenshots.
Add a short "Architecture" pointer to `docs/superpowers/specs/`.

---

### Task 9: Build, verify, commit

- [ ] **Step 1: Install from source (replaces the PyPI install)**

Run:
```
uv tool install "C:/Projects/Claude Widget" --force
```
Expected: builds the wheel from src/, installs `claude-usage` console script.

- [ ] **Step 2: Verify CLI + import**

Run:
```
claude-usage --version
claude-usage --once
```
Expected: prints version (0.6.6); `--once` prints a JSON stats blob (redacted) without error.

- [ ] **Step 3: Run the test suite**

Run:
```
uv tool run --from "C:/Projects/Claude Widget" --with pytest pytest "C:/Projects/Claude Widget/tests"
```
(or, if a project venv is preferred: `uv run --extra dev pytest`)
Expected: all tests pass (7 tests across config + history).

- [ ] **Step 4: Smoke-launch the OSD**

Run (PowerShell):
```powershell
Start-Process "$env:USERPROFILE\.local\bin\claude-usage.exe"
Start-Sleep 4
(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'claude-usage\.exe' }) -ne $null
```
Expected: process running (True) → OSD launches from the source-built install.

- [ ] **Step 5: Commit foundation**

Run:
```
git -C "C:/Projects/Claude Widget" add pyproject.toml LICENSE NOTICE setup.ps1 README.md tests/
git -C "C:/Projects/Claude Widget" commit -m "build: full-source packaging (pyproject, LICENSE, NOTICE, tests); setup.ps1 installs from source"
```

- [ ] **Step 6: CHECKPOINT** — report results to the user; do NOT push yet (pushing to the public repo is outward-facing — confirm first). Then proceed to the Phase 1 plan.

---

## Self-Review

**Spec coverage (Phase 0 rows):** repo continuity (D2) → Task 1; full-source fork + merge patches (D1) → Tasks 2–3; pyproject/src layout → Task 4; LICENSE+NOTICE → Task 5; tests scaffold → Task 6; setup.ps1 from source (fixes patch-drift) → Task 7; README → Task 8; install + verify acceptance criteria → Task 9. All Phase 0 deliverables/acceptance items covered.

**Placeholder scan:** none — all file contents and commands are concrete.

**Type/name consistency:** package import name `claude_usage`, dist name `claude-usage-widget`, console script `claude-usage` (= `claude_usage.cli:main`), dep `PySide6-Essentials>=6.5,<7` — consistent across pyproject, setup.ps1, tests, and verify steps. Vendoring source path consistent in Tasks 2/3.

**Open risk:** `[tool.setuptools.dynamic] version = {attr=...}` under src layout — if version resolution fails at build (Task 9 Step 1), fall back to a static `version = "0.6.6"` in `[project]` (remove the `dynamic`/`[tool.setuptools.dynamic]` lines). Caught by the Task 9 install step.
