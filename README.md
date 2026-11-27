# Claude Usage for Windows

A small Windows app that shows how much of your Claude plan limits you have used, and what your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) usage would cost at API prices. It reads the transcripts Claude Code keeps on your machine and the usage endpoint Claude Code itself calls.

This is a full-source, Windows-focused fork of [`claude-usage-widget`](https://github.com/bozdemir/claude-usage-widget) by Burak (MIT). The whole app lives in [`src/claude_usage/`](src/claude_usage) and installs straight from this repo.

## What you get

**The overlay.** A small always-on-top pill or card. It leads with the limit that will run out first and shows when it resets. It shows only the limits your plan reports: Ultra has weekly limits and no 5-hour limit, so you see the weekly limit and never a 5-hour 0%; on a plan with a 5-hour limit, the card shows both. The card also shows today's cost. Meters turn amber at 75% and red at 90% (you can change both). When the numbers are old they are dimmed with an "as of" time, and when you are signed out it says so instead of showing numbers. Click it to open the app, right-click for the menu, drag it anywhere (over the taskbar is fine); the position is remembered.

**The app**, with three pages:

- **Overview**: your plan limits (weekly pace and projection, past weeks, other limits and extra usage), cost, tokens, sessions, agent time and cache hit rate with the change from the previous period, a timeline by project or by model, top sessions, an activity heatmap, and the cost split by model. Pick 24 hours, 7, 30 or 90 days, or all time, and narrow everything to one project from the sidebar.
- **Sessions**: the sessions in the range (the 2,000 costliest when there are more), with search, an Interactive or Automated filter and sortable columns.
- **Settings**: appearance (System, Light, Dark), overlay style, opacity and keep on top, launch at startup, limit alerts and their thresholds, the data store and re-index, version and update check.

**The tray icon** opens the app on click. The tray and the overlay share one menu: Open Claude Usage, Refresh now, Pill or Card, Settings, Quit. Its first line, like the tray tooltip, sums up the limits your plan reports, the one that will run out first leading as on the overlay (for example "Weekly 67%, resets Tue 8:00 PM"), the weekly Fable limit while you use it, and the live token rate. When the numbers are old, the tooltip also says why. Limit alerts arrive as tray notifications, once per crossing in each limit window, for the weekly limit, the 5-hour limit when your plan has one, and the Fable limit while it is active.

## How cost is computed

Cost is API-equivalent: every API call in your local Claude Code transcripts, subagents included, priced at Anthropic's published per-token rates, with 5-minute and 1-hour cache writes priced separately. It is what those tokens would cost on the API, not your bill. Claude Code's own `/cost` can read a few percent higher because it also counts requests it does not write to transcripts, such as web searches and title calls. Subagent transcripts written by Claude Code builds before about October 2026 often kept only the first streamed line of each reply, so their output tokens, and the cost of those days, read low; the app does not guess the missing part.

## Where your data lives

- **Usage store**: `%LOCALAPPDATA%\claude-usage\usage.db`, a SQLite file with one row per API call (tokens, model, time, working folder and project name), session titles that Claude Code wrote, turn times and the limit readings over time. Cost is not stored; it is worked out from tokens when shown, so a price fix corrects all history.
- **Settings**: `%USERPROFILE%\.config\claude-usage\config.json`, holding only what you changed. Settings from older versions (theme, minimized overlay) are carried over. A file that does not parse is kept as `config.json.bad` and the app starts on defaults. A file that is locked at start (by a sync tool, say) is left as it is: that run uses the defaults, and your changes are added to the file once it can be read.
- **Log**: `%USERPROFILE%\.claude\claude-usage.log`.

Claude Code deletes old transcripts after `cleanupPeriodDays` (a Claude Code setting). The store keeps every call it has read, so your history stays after the transcripts are gone. Days from before the store existed come from the older `usage-daily.jsonl` totals (cost and tokens only) and are marked as such in the app.

The first start reads all your transcripts. On a large history (tens of GB) that takes several minutes; the app shows the progress and the totals fill in as it goes. Re-index (Settings, or `claude-usage --reindex`) reads everything again and never drops history that is already in the store.

## Privacy

- The only network requests go to Anthropic, for your plan limits with your Claude Code sign-in, and to GitHub, for this repo's latest release. No telemetry.
- The store holds counts and metadata, not the text of your prompts or replies.
- In `~/.claude` the app writes its log and lock file. If the limits request keeps failing to sign in, it runs `claude setup-token` (a few times at most) to make a long-lived token in `~/.claude/claude-usage-token`. Without that token it refreshes Claude Code's sign-in in `~/.claude/.credentials.json` when it is about to expire, as Claude Code does.

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10 or 11 | 64-bit; built for Windows 11 |
| Python 3.10+ | Through [uv](https://docs.astral.sh/uv/); the setup script handles it |
| Claude Code | Signed in at least once (`claude` in a terminal) |

## Install

```powershell
git clone https://github.com/arora-vaibhav/claude-usage-widget-windows.git
cd claude-usage-widget-windows
powershell -ExecutionPolicy Bypass -File setup.ps1        # add -Autostart to start at sign-in
```

Then double-click "Claude Usage" on your desktop. Starting it again while it runs opens the app window.

To update: `git pull`, then run `setup.ps1` again.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Uninstall
```

This removes the launcher, the desktop shortcut, the autostart entry and the `claude-usage-widget` uv tool. Your store and settings stay; delete `%LOCALAPPDATA%\claude-usage` and `%USERPROFILE%\.config\claude-usage` to remove them too.

## Command line

| Command | What it does |
|---|---|
| `claude-usage` | Starts the app (or opens its window if it is already running) |
| `claude-usage --version` | Prints the version |
| `claude-usage --json [--range 24h\|7d\|30d\|90d\|all]` | Updates the store, fetches the limits and prints the snapshot the app shows, as JSON (default range 7d) |
| `claude-usage --reindex` | Reads every transcript again |

## Development

```powershell
uv tool install -e .     # editable install

# tests, headless
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"
uv run --no-project --with pytest --with PySide6-Essentials python -m pytest tests
```

- Layout: `src/claude_usage/` (the package; `app/qml/` holds the app window), `tests/unit/`, `tests/fixtures/` (synthetic data only), `launcher/`, `setup.ps1`.
- One runtime dependency: `PySide6-Essentials` 6.7 or later (Qt Quick for the app, a QWidget for the overlay, SQLite from the standard library).
- Design: [`docs/superpowers/specs/2026-11-26-native-app-design.md`](docs/superpowers/specs/2026-11-26-native-app-design.md).

## Contributing

PRs welcome. For a Windows bug, open an issue with your Python and uv versions and the relevant lines of `~/.claude/claude-usage.log`.

## License

MIT. This fork vendors the upstream source under the same license; see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

## Support the project

If this saves you time or tokens, buy me a matcha.

**BTC:** `bc1qk437xlm0rl8pdq6akhy543j66urqscg7ntx39470dgxm5gc7zudqypcfz6`
