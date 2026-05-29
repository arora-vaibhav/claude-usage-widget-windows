# Claude Usage Widget — Windows Edition

A Windows-native desktop overlay that shows your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) usage in real time — session %, weekly %, reset timers, cost breakdown, and a weekly AI summary.

This is a **full-source, Windows-focused fork** of [`claude-usage-widget`](https://github.com/bozdemir/claude-usage-widget) by Burak (MIT). The complete application source lives in [`src/claude_usage/`](src/claude_usage) and is installed directly from this repo — there is no separate PyPI download or patch-overlay step.

---

## What's different from upstream

| Area | Change |
|---|---|
| **Packaging** | Full source vendored in `src/`; installs from this repo via `uv tool install .` (no PyPI + patch overlay) |
| **Encoding** | File reads use `encoding="utf-8"` / `utf-8-sig` to avoid `UnicodeDecodeError` and BOM issues on Windows (cp1252 default) |
| **OAuth auto-refresh** | Expired tokens refresh automatically from the stored `refreshToken` — no manual re-auth |
| **Rate limiting** | Token refresh has a cooldown so a broken endpoint isn't hammered |
| **Memory-safe scans** | Conversation/cache/ticker scans use byte budgets + `MemoryError` guards for large `~/.claude/projects` trees |
| **No terminal required** | A VBScript launcher runs the widget without a console window |
| **Desktop shortcut** | Setup creates a one-click `.lnk` on your Desktop |
| **Position memory** | Drag the widget anywhere; position is saved and restored on next launch |
| **Details panel** | Wider (680 px), auto-fits height to content, positions next to the OSD widget |

---

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10 / 11 | 64-bit |
| Python 3.10+ | Via [uv](https://docs.astral.sh/uv/) — installer handles this |
| [uv](https://docs.astral.sh/uv/) | Installed automatically if missing |
| Claude CLI | For initial authentication — [install guide](https://docs.anthropic.com/en/docs/claude-code) |
| Claude subscription | Pro / Max / Team / Enterprise or API key |

---

## Quick start

```powershell
# 1. Clone this repo
git clone https://github.com/arora-vaibhav/claude-usage-widget-windows.git
cd claude-usage-widget-windows

# 2. Run the setup script (installs from source, one time)
powershell -ExecutionPolicy Bypass -File setup.ps1

# 3. Authenticate Claude CLI (if not done already)
claude   # follow the browser auth flow

# 4. Double-click "Claude Usage" on your Desktop
```

The widget appears as a small translucent overlay — drag it wherever you want.

---

## How to use

| Action | Result |
|---|---|
| **Click** the widget | Opens the details panel |
| **Drag** the widget | Moves it; position is saved automatically |
| **Right-click** | Context menu: settings, theme, quit |
| **Close** details panel | Just close the window; the OSD stays |

---

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Uninstall
```

Removes the launcher, desktop shortcut, and the `claude-usage-widget` uv tool.

---

## How it works

- `setup.ps1` installs the widget straight from this repo's source with `uv tool install .` — the code in `src/claude_usage/` *is* the app, so nothing can drift out of sync.
- The VBScript launcher in `launcher/` calls the installed `claude-usage.exe` with `WindowStyle=0`, keeping the GUI visible while hiding the console.
- Token refresh runs inside the widget process using the `refreshToken` already stored in `~/.claude/.credentials.json` — no credentials are transmitted to this repo or any third party.
- Usage data comes only from local `~/.claude/` files and the Anthropic rate-limit API.

---

## Development

```powershell
# Editable install for development
uv tool install -e .

# Run the test suite (headless, pure-logic tests)
uv run --extra dev pytest
# or, against the source tree without installing:
#   $env:PYTHONPATH="src"; uv run --no-project --with pytest python -m pytest tests
```

- **Layout:** `src/claude_usage/` (package), `tests/unit/` (tests), `launcher/`, `setup.ps1`.
- **Build:** setuptools (`pyproject.toml`), single runtime dependency `PySide6-Essentials`.
- **Design & roadmap:** see [`docs/superpowers/specs/`](docs/superpowers/specs) for the modernization plan (stabilization, a usage-history store + dashboard, and UI work).

---

## Contributing

PRs welcome. For a Windows-specific bug, open an issue with your Python version, uv version, and console output — run `claude-usage` directly in a terminal to see logs.

---

## License

MIT. This fork vendors the full upstream source under the same MIT License — see [`LICENSE`](LICENSE) (© 2026 Burak) and [`NOTICE`](NOTICE) for attribution and a summary of fork changes.

---

## Support the Project

If this saves you time or tokens — buy me a matcha. ☕

**BTC:** `bc1qk437xlm0rl8pdq6akhy543j66urqscg7ntx39470dgxm5gc7zudqypcfz6`
