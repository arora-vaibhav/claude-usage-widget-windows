# Claude Usage as a native app: design

Date: 2026-11-26. Status: approved by the owner as "full autonomous" (design calls are the builder's, recorded here).
Supersedes decision D6 (custom QPainter charts) and softens D4 (JSONL store) of `2026-05-29-claude-usage-widget-modernization-design.md`.

## Why

The widget is useful but reads as unfinished. The owner's verdict: the UI is not modern, the 11 themes are all half done, and the history and analytics are wrong. The quality bar is the Ponytail board (sidebar, KPI cards with sparklines and deltas, stacked timeline with a metric toggle, range control, sessions table, activity heatmap, light and dark), built as a proper desktop app rather than a web page.

The November 2026 audit measured the problems:

- History under-reports by 22% ($8.4k) and 36% of tokens over 08-19..11-26. Subagent transcripts are skipped, the price table predates the 5.x models and 1h cache writes, and day rows are copied across an overnight sleep and then frozen by a max() merge.
- The 5-hour limit has been null since 2026-10-30 (the Ultra plan has none) but the app shows 0%. Weekly is the binding limit, and the OAuth payload carries more than the app reads (scoped Fable limit, surface breakdown, extra usage).
- The running widget burns 15% of a core and logged 58 MemoryErrors because it re-reads every recently changed transcript every 30 s.
- Every font falls back to Courier New or Arial. The minimized OSD is a 6px line. 29% of the code is themes and skins, none finished.
- Claude Code deletes transcripts after `cleanupPeriodDays` (14 here), so history must be kept in an app-owned store.

## Goals

1. Correct numbers: every API call counted once (subagents included), priced with current per-model rates, bucketed by its own local timestamp, kept after Claude Code cleans up.
2. A native Windows 11 app window at the Ponytail level: Overview, Sessions and Settings pages, project scoping, light/dark/system.
3. A glanceable OSD that leads with the binding limit and never shows a misleading 0%.
4. Light at idle: under 2% of a core averaged over an hour, no MemoryError.
5. Less code: one design system, one popup surface (the app), no dead features.

## Non-goals

- A Claude desktop mod or status-line companion.
- Multiple OSD layouts or themes beyond Light, Dark and System.
- Any network listener, webhooks, the AI weekly summary, the ticker tape.
- Telemetry, accounts, sync, cross-platform polish (Linux/macOS keep working where Qt does, untested).

## Anti-goals (failure even if it works)

- Pretty charts over wrong numbers.
- A web page in a window (the owner asked for a proper app).
- Writing into Claude Code's files or rotating its credentials differently than today.
- Showing private project names or costs in anything committed to the repo.

## Constraints

- Only dependency stays `PySide6-Essentials` (6.11.1 installed). It ships QtQuick, QtQuick.Controls (FluentWinUI3 style), QtQuick.Shapes, QtQuick.Effects, QtNetwork, sqlite3 is stdlib. QtGraphs, QtCharts and QtWebEngine are not available.
- Python 3.10+ (runs on 3.14 here). Windows 11 first.
- The installed tool is an editable install of this checkout's `src`, so development happens in a worktree.
- Real-data renders and snapshots stay in scratch folders, never in the repo. Fixtures are synthetic.

## Approaches considered

| Approach | Install | Feel | Verdict |
|---|---|---|---|
| Polish the QWidgets/QSS/QPainter UI | 0 | No shadows, transitions or easing; QSS cascade bugs; every chart hand-painted | Rejected: the current ceiling is the problem |
| **Qt Quick + FluentWinUI3, one process** | 0 | Native Windows 11 controls, GPU rendering, animation, Shapes for charts; 0.35-1.0 s to first frame | **Chosen** |
| pywebview / WebView2 window | +4 MB, 6 packages | Very high ceiling, but it is a web view | Rejected by the owner |
| Edge `--app` window on a generated HTML file | 0 | Browser window identity | Rejected by the owner |

## Architecture

One process, as today. The GUI thread owns the OSD (QWidget), tray, menus and the app window (QQmlApplicationEngine, created on first open, hidden on close). One worker thread does all collection.

```
worker (every refresh_seconds):
  ingest.tail(store)            -> new API calls, turns, titles
  collector.fetch_rate_limits() -> limits dict (richer than today)
  store.add_sample(...)         -> utilization history (null kept as null)
  snapshot.now(store, limits)   -> "now" dict
  emit refreshed
GUI thread:
  overlay.set_now(now); tray tooltip; notifier thresholds
  app backend: if window visible, rebuild snapshot for the current range/project
```

### Modules (target)

- `store.py` (new): SQLite at `%LOCALAPPDATA%\claude-usage\usage.db` (WAL, busy_timeout). Schema, migrations, legacy import.
- `ingest.py` (new): incremental transcript tailer and the first full index with progress. Replaces `token_scan.py`, `live_stream.py`, `subagents.py`, `daily.py`, `history.py`.
- `pricing.py` (rewritten table): per-model input, output, cache read, 5m write, 1h write; family fallback marked `family`; unknown marked `unpriced`.
- `collector.py` (trimmed): credentials, token refresh, `fetch_rate_limits` returning the full limits model. `UsageStats` and `collect_all` go away.
- `snapshot.py` (new, pure): `build(conn, range_key, project, now, limits) -> dict` and `now(...) -> dict`. Contract: `tests/unit/snapshot_contract.py`.
- `tokens.py` (new): the design tokens. Replaces `themes.py`.
- `app/` (new): `window.py` (engine, `Backend` QObject bridge) and `qml/` (pages and components).
- `overlay.py` (rewritten): pill and card, states, tokens.
- `widget.py` (slimmed): controller, worker, tray, menus, settings, single instance.
- Kept: `config.py`, `autostart.py`, `auth_watchdog.py`, `logging_setup.py`, `winchrome.py`, `notifier.py` (switched to tray balloons), `updater.py` (fixed), `cli.py`.
- Deleted: `skins/`, `dashboard/`, `themes.py`, `ticker.py`, `webhooks.py`, `ai_report.py`, `exporter.py`, `api_server.py`, `cache_analyzer.py`, `analytics.py`, `trends.py`, `forecast.py` and the old view classes in `widget.py`.

### Store schema

- `meta(key PRIMARY KEY, value)`: schema version, index progress, coverage dates.
- `files(path PRIMARY KEY, size, mtime, offset)`: tail position per transcript.
- `calls(msg_id PRIMARY KEY, ts_ms, day, hour, session_id, agent_id, is_subagent, project, cwd, model, speed, input, output, cache_read, cache_write_5m, cache_write_1h, web_search)`: one row per unique API call. Indexed on `day`, `session_id`, `ts_ms`.
- `turns(uuid PRIMARY KEY, session_id, ts_ms, day, project, duration_ms)`: from `system`/`turn_duration` records, for agent time and turn counts.
- `sessions(session_id PRIMARY KEY, title, entrypoint, cwd, project)`: metadata only; numbers are aggregated from `calls`.
- `samples(ts PRIMARY KEY, weekly, session, payload)`: utilization; `NULL` when not reported.
- `legacy_days(day PRIMARY KEY, cost, tokens, messages, quality)`: old `usage-daily.jsonl` rows, `quality` is `legacy` or `corrupt`.

Cost is never stored. It is computed from tokens and the price table when read, so a price fix corrects all history.

### Ingest rules

- Walk `~/.claude/projects/**/*.jsonl` recursively (main sessions and `subagents/**`, including workflow runs).
- Per file, read from the stored offset to the last complete newline. If the file shrank or its mtime went back, re-read from 0 (dedupe makes that safe). Skip any line over 8 MB after advancing past it.
- Cheap byte prefilter before `json.loads`: only lines that can be an assistant message with usage, a `turn_duration` record, or a title record.
- One row per `message.id`. Claude Code writes a message on several lines while it streams, and early lines can carry a partial `output_tokens` (measured: keeping the first line drops 10.8% of output tokens). So upsert: every usage field keeps the maximum seen across the message's lines, the timestamp keeps the earliest. Skip `<synthetic>` models.
- Claude Code's own `cost-state` records (`modelUsage.<model>.costUSD` per session) are an independent check on pricing, not a source of truth. Published per-token prices are the truth for "API-equivalent"; any systematic gap is explained and shown in the UI's cost note.
- `day` and `hour` come from the call's own timestamp converted to local time. Never from `now()`.
- Project = basename of the record's `cwd`, with `\.claude\worktrees\<x>` and `.worktrees\<x>` folded to the repo folder, the home folder shown as "Home", and anything under the system temp folder grouped as "Temporary folders" (never a raw path). Fallback: the decoded project directory name.
- `is_subagent` = the file lives under a `subagents` directory. Its session is the parent session id.
- The first run indexes everything in the worker and reports `{done, total}` files; the UI shows progress. Target: the full 40 GB in a few minutes, with steady-state ticks under 50 ms when nothing changed.

### Pricing (USD per million tokens: input / output / cache read; 5m write = 1.25x input, 1h write = 2x input)

Fable 5.2 8/40/0.20; Fable 5.1 10/50/0.25; Fable 5 10/50/1.00; Opus 5.5 4/20/0.20; Opus 5, 4.8, 4.7, 4.6, 4.5 5/25/0.50; Sonnet 5.5 2/10/0.10; Sonnet 5 2/10/0.20; Sonnet 4.6 and 4.5 3/15/0.30; Haiku 5.5 0.10/0.50/0.01 (0.50/2.50/0.05 above 100K prompt tokens); Haiku 4.5 1/5/0.10. Fast mode doubles Opus 5.5. Web search $0.01 per request. The builder verifies these against the claude-api skill's model table before shipping.

### Legacy history

Import `~/.claude/usage-daily.jsonl` read-only into `legacy_days`. A row whose cost and message count equal the previous day's row is `corrupt` (the overnight copy bug), as are the pre-dedupe rows 2026-05-25..06-02. Days before `coverage_from` use legacy rows (badged in the UI, cost and tokens only); from `coverage_from` on, the ledger is the source. `coverage_from` is the first day after which every legacy day with cost has ledger cost of at least half the legacy cost. Import `usage-history.jsonl` into `samples`, treating a 5h value of exactly 0 as `NULL`.

### Limits

`fetch_rate_limits` keeps today's two paths (OAuth `/api/oauth/usage` via curl, unified headers for long-lived tokens) and its error strings, and returns in addition: weekly `{pct, resets_at, severity}`, five-hour (`None` when the payload has `null`), `limits[]` scoped entries joined with `weekly_scoped_shares`, `seven_day_breakdown.rows`, `seven_day_breakdown.window_started_at`, and `extra_usage`. Missing values stay `None`, never `0.0`.

### Snapshot

`tests/unit/snapshot_contract.py` is the authoritative shape. Highlights:

- `now`: range-independent; drives the OSD. `primary` is the limit projected to run out first (weekly when the 5h limit is absent).
- `limits.weekly`: pace engine. `elapsed` = share of the window gone; `pace` = pct / elapsed; `eta` = when 100% is reached at the faster of the 24 h rate and the window-average rate; `usual` and `usual_series` = median of past complete windows at the same elapsed share; `locked_hours` = time between eta and reset; `daily_budget` = what per day keeps you under 100%.
- `kpis`: cost, tokens, sessions, agent time, cache hit rate, each with the previous equal-length window (to the same hour) and a sparkline. Deltas are `null` when the previous window reaches past `coverage_from` or for `all`.
- `timeline`: buckets are hour (24h), 6h (7d), day (30d, 90d), week (all). The in-progress bucket is flagged by `partial_last` and drawn as "so far". Series by project and by model, top 5 plus Other. Colour index = the entity's all-time cost rank, so colours never change with the range; Other is `-1`.
- `sessions`: every session with a call in range (cap 2,000), with title when Claude Code stored one.
- `activity`: last 84 days, heatmap levels 0-4, streaks and pre-formatted facts.
- Range keys: `24h`, `7d` (default), `30d`, `90d`, `all`. Project filter applies to everything except `limits` and `now`.

Performance target: `build` under 200 ms on the real store.

## The app window

Qt Quick, `QQuickStyle.setStyle("FluentWinUI3")`, one `ApplicationWindow` (1280x820 default, min 960x640, geometry remembered). Dark title bar and rounded corners through `winchrome.apply_win11_chrome` when dark. Colours come from `tokens.py` via the `Backend`. Segoe UI Variable throughout, `tnum` on every number.

- **Sidebar (248px):** brand mark and "Claude Usage"; pages Overview, Sessions, Settings; "Projects" with All projects and each active project (colour dot, session count), "Show N idle"; footer with live dot, "Updated 12s ago", indexing progress when running.
- **Overview:** header (project name or "All projects", subtitle with session count and dates, range segmented control, refresh). Plan limits row: weekly card (big %, reset countdown, pace chart with actual line, dashed projection to 100%, shaded "limited" region, dotted usual week, pace/usual/budget/burn stats), past weeks card (bars of hours at the limit per week, "9 of 12 weeks hit 100%"), other limits card (5-hour status, scoped limits, extra usage, surface chips). Usage row: 5 KPI cards with value, delta chip, note and an edge-to-edge sparkline. Timeline card: big total, metric toggle (Cost, Tokens, Messages, Agent time), group toggle (Project, Model), smoothed stacked area, hover crosshair tooltip with per-series values, legend chips that hide a series, "so far" marker. Bottom: top sessions (10, link to Sessions page) and Activity (hex heatmap, active days, streak, facts). Models and cost card: share bar, model table, cost split tiles, caching saved line. Footer: cost is API-equivalent, not a bill; legacy coverage note.
- **Sessions:** search box, filter (All, Interactive, Automated), sortable columns (started, project, agent time, turns, tokens, cost, cache hit, subagents), 50 rows at a time with "Show more".
- **Settings:** Appearance (System, Light, Dark); OSD (style: Pill or Card, opacity, always on top); Launch at startup; Notifications (on/off, caution and critical thresholds); Data (store location, transcripts indexed, coverage, Re-index); About (version, update check).
- States: first-run indexing (progress and partial numbers), signed out (call to action), stale (as-of time), empty project.
- Motion: 120-150 ms hover and value transitions; no continuous animation.

## The OSD

QWidget painter on the same tokens. Two styles:

- **Pill** (replaces the 6px capsule; default when the user had `osd_minimized`): 32px tall, status dot, primary limit first ("7d 46%" with a 3px meter and "5d 17h"), then the other limit if reported. Hover raises opacity to 1 and shows a tooltip with the forecast.
- **Card**: 296x136, header "Claude usage" plus a status chip; two columns (or one when 5h is absent) with label, 28px value, 4px meter, and "Resets in 2h 30m" or the forecast in caution/critical colour; footer "Today $82".

Rules: values always in the text colour; colour only on meters and icons; levels from config thresholds (75/90); plate alpha at least 0.85 under text, user opacity applies to the whole window only when idle and normal; stale shows values dimmed with "as of 14:02"; signed out shows "Sign in" and no numbers; no overlapping badges; sizes on a 4px grid and integer device pixels. Click opens the app, right-click opens the menu, drag moves it (position and taskbar parking behave as today).

## Menus, tray, single instance

One menu for OSD right-click and tray: Open Claude Usage, Refresh now, Pill/Card, Settings..., Quit. Styled from tokens, Windows 11 metrics. Launching a second copy (desktop shortcut while running) asks the first over `QLocalServer` to open the app window, then exits.

## Config migration

`theme` becomes `appearance`: dracula, nord, catppuccin-mocha, gruvbox-dark, default, dashboard, hud, terminal, strip map to `dark`; receipt and brutalist to `light`; anything else `system`. `osd_minimized: true` maps to `osd_style: "pill"`, else `"card"`. `show_ticker`, `osd_view_mode`, `webhooks`, `api_server_*` are dropped on save. Opacity default becomes 1.0; an existing value is kept.

## Testing and verification

- Unit: store migrations, ingest (nested subagent file, repeated message id, the same id in a resumed file, partial last line, file shrink, a refresh across midnight, 5m vs 1h writes), pricing per current model, limits parsing (null five_hour, scoped, extra usage), snapshot contract and consistency on a synthetic store, config migration.
- Render: offscreen grabs of the app (light and dark, Overview and Sessions and Settings) and the OSD (every state, both styles) from synthetic fixtures; assert no QML warnings.
- Real data (scratch only): full index of the owner's transcripts, totals reconciled with the audit recount ($37,644.54 for 08-19..11-26), snapshot timing, CPU over 10 minutes of running.

## Left to the builder

Exact component structure in QML, chart smoothing method, icon set (Segoe Fluent Icons glyphs are available on Windows 11), the heatmap shape (hex or square), and copy wording, all judged against the Ponytail board and the audit prototype screenshots.
