# Claude Usage Widget â€” Windows Edition

A Windows-native desktop overlay that shows your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) usage in real time â€” session %, weekly %, reset timers, cost breakdown, and a weekly AI summary.

This is a community Windows port of [bozdemir/claude-usage-widget](https://github.com/bozdemir/claude-usage-widget), which originally targeted Linux/macOS. All credit for the core product goes to the original author.

![Widget screenshot](assets/screenshot-widget.png)

---

## What's different from upstream

| Area | Change |
|---|---|
| **Encoding** | All file reads use `encoding="utf-8"` to avoid `UnicodeDecodeError` on Windows (cp1252 default) |
| **OAuth auto-refresh** | Expired tokens are refreshed automatically using the stored `refreshToken` â€” no manual re-auth |
| **Rate limiting** | Token refresh has a 5-minute cooldown so a broken endpoint isn't hammered |
| **No terminal required** | A VBScript launcher runs the widget without a console window |
| **Desktop shortcut** | Setup creates a one-click `.lnk` on your Desktop |
| **Position memory** | Drag the widget anywhere; position is saved and restored on next launch |
| **Details panel** | Wider (680 px), auto-fits height to content, positions next to the OSD widget |
| **Active sessions** | Removed from the panel (not functional on Windows) |

---

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10 / 11 | 64-bit |
| Python 3.11+ | Via [uv](https://docs.astral.sh/uv/) â€” installer handles this |
| [uv](https://docs.astral.sh/uv/) | Installed automatically if missing |
| Claude CLI | For initial authentication â€” [install guide](https://docs.anthropic.com/en/docs/claude-code) |
| Claude subscription | Pro / Max / Team / Enterprise or API key |

---

## Quick start

```powershell
# 1. Clone this repo
git clone https://github.com/v-zagora/claude-usage-widget-windows.git
cd claude-usage-widget-windows

# 2. Run the setup script (one time only)
powershell -ExecutionPolicy Bypass -File setup.ps1

# 3. Authenticate Claude CLI (if not done already)
claude   # follow the browser auth flow

# 4. Double-click "Claude Usage" on your Desktop
```

That's it. The widget appears as a small translucent overlay â€” drag it wherever you want.

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

This removes the launcher, desktop shortcut, and the `claude-usage-widget` uv tool.

---

## How it works

- `setup.ps1` installs the upstream `claude-usage-widget` via `uv tool install`, then overlays the three patched Python files from `patches/`.
- The VBScript launcher in `launcher/` calls the installed `claude-usage.exe` with `WindowStyle=0`, keeping the GUI visible while hiding the console.
- Token refresh runs inside the widget process using the `refreshToken` already stored in `~/.claude/.credentials.json` â€” no credentials are transmitted to this repo or any third party.

---

## Patches overview

### `patches/collector.py`
- All `open()` calls use `encoding="utf-8", errors="replace"` to handle Windows cp1252 defaults
- `_refresh_access_token_if_needed()`: auto-refreshes the OAuth token before API calls; 5-minute cooldown; atomic file write via `tmp + os.replace()`

### `patches/overlay.py`
- Added `positionSaved = Signal(int, int)` Qt signal
- `mouseReleaseEvent`: emits position signal after a drag ends so the main app can persist it

### `patches/widget.py`
- `_save_osd_position()`: writes `osd_x` / `osd_y` to user config on drag
- Restores saved position on startup
- `_fit_height()`: measures inner layout height directly (bypasses `QScrollArea`) and resizes the popup to fit content, capped at 88% of screen
- Details panel: 680 px wide, smart positioning relative to OSD, section headers don't word-wrap
- Active sessions section disabled (not functional on Windows)

---

## Contributing

PRs welcome. If you find a Windows-specific bug, open an issue with your Python version, uv version, and the error output from the console (run `claude-usage.exe` directly in a terminal to see logs).

---

## Credits

- **Original widget**: [bozdemir/claude-usage-widget](https://github.com/bozdemir/claude-usage-widget) â€” all core logic, UI, and design
- **Windows port**: [v-zagora](https://github.com/v-zagora)

## License

This repo contains only the Windows-specific patches and setup tooling. The patched source files remain under the same license as the upstream project. See [upstream LICENSE](https://github.com/bozdemir/claude-usage-widget/blob/main/LICENSE) for details.


---

## Support the Project

If this saves you time or tokens — buy me a matcha. ☕

**BTC:** `bc1qk437xlm0rl8pdq6akhy543j66urqscg7ntx39470dgxm5gc7zudqypcfz6`