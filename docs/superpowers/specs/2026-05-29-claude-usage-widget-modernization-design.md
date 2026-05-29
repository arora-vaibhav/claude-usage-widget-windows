# Claude Usage Widget → Modern App — Design Spec

- **Date:** 2026-05-29
- **Status:** Approved (design); pending spec review → implementation plan
- **Owner:** Vaibhav (arora-vaibhav)
- **Repo (source of truth):** https://github.com/arora-vaibhav/claude-usage-widget-windows
- **Upstream:** `claude-usage-widget` v0.6.6 by Burak — https://github.com/bozdemir/claude-usage-widget (MIT)

---

## 1. Context & current state

`claude-usage-widget` is a cross-platform PySide6 floating desktop widget + CLI that
shows real-time Claude Code usage. It reads `~/.claude/history.jsonl` and the
`~/.claude/projects/*/*.jsonl` conversation transcripts for message/token data,
fetches rate-limit utilization from the Anthropic API (via the OAuth credentials in
`~/.claude/.credentials.json`), and renders a frameless on-screen-display (OSD).

The app is already feature-rich: themes/skins, OSD overlay (bars/gauge modes), a
per-turn cost ticker, notifications, a localhost JSON API, webhooks, forecast/trends/
analytics (anomaly detection), an AI report (Haiku), CSV/JSON export, and sparklines.

**The user's distribution is a *patch overlay*, not a full app.** The GitHub repo
`claude-usage-widget-windows` tracks only 5 patched files (`patches/{cache_analyzer,
collector,overlay,ticker,widget}.py`), a launcher VBS, `setup.ps1`, and a README.
`setup.ps1` runs `uv tool install claude-usage-widget` then copies the patches over the
installed `site-packages`.

### Problems found in the current workflow
1. **`setup.ps1` patch-drift:** the script only copies `collector.py, overlay.py,
   widget.py` — but `patches/` also contains `cache_analyzer.py` and `ticker.py`, which
   are never applied. A fresh install silently misses two fixes.
2. **No full source under version control** — only 5 of ~25 modules. Real feature/UI
   work means editing installed `site-packages` directly, which `uv tool upgrade` wipes,
   can't be diffed/reviewed, and has no test harness.
3. **History recording stalled** — last sample in `usage-history.jsonl` is 2026-05-23,
   though Claude has been used since (Claude's `history.jsonl` updated 5/26). Root cause
   not yet diagnosed; two leading hypotheses: (a) no autostart, so the widget simply
   isn't running after reboot/close; (b) the heavy 30 s collection occasionally throws
   before `append_sample`, silently skipping records.
4. **`claude_dir` stored with mixed slashes** (`C:\Users\V/.claude`) — works, but a
   smell that can bite path comparisons.
5. **"History" UX is two sparklines** ("Last 5 hours", "Last 7 days") buried in the
   details popup — even though `trends.py`/`forecast.py`/`analytics.py` already compute
   far more. The data largely exists; the UI doesn't surface it.

### Key data finding (good news)
The collector **already computes** everything the user wants for history — `today_cost`,
`week_cost`, `today_by_model_detailed` (`{model: {input,output,cache_read,cache_creation}}`),
`today_by_project`, tokens, and messages. It simply **never persists** them: the on-disk
history (`usage-history.jsonl`) stores only `{ts, session, weekly}` (two utilization
floats). So the history feature is mostly a **storage + view** problem, not a computation
one. And because the `~/.claude/projects/*.jsonl` transcripts carry per-message
model/token/timestamp/project data (already parsed by the collector), historical data can
be **backfilled** rather than starting empty.

---

## 2. Goals & non-goals

### Goals
- Turn the patch-overlay into a **maintainable full-source fork** under git, with tests
  and a clean local install path — without losing the existing launcher/setup/README or
  the public repo's history.
- **Stabilize:** the widget records reliably, runs continuously (autostart), surfaces
  failures (real logging), enforces single-instance, and shows correct/fresh numbers.
- **Usage history** the user can actually read: usage % over time, cost over time,
  tokens & messages per day, and per-model / per-project breakdowns — backfilled from
  existing transcripts so it's useful immediately.
- **Modern app feel:** a real Dashboard window (not just the cramped OSD popup), a
  cohesive restyle, tray presence, and smooth interactions.

### Non-goals (for now)
- Re-architecting the cross-platform (Linux/macOS) paths — changes stay
  cross-platform-safe, but Windows is the validation target.
- Publishing the fork to PyPI under a new name (can revisit later).
- Changing the data sources (still `~/.claude` + Anthropic rate-limit API).
- Replacing the OSD's fundamental "floating widget" concept.

---

## 3. Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Development model | **Full-source fork** — vendor complete v0.6.6 into the repo; merge the 5 patches into the source permanently. |
| D2 | Repo continuity | **Continue the existing GitHub repo** (`claude-usage-widget-windows`) — preserve history, remote, launcher, setup, README; restructure it into a full package. |
| D3 | App shape | **Keep the floating OSD widget** as the glance surface; **add a Dashboard window** for history/stats/settings. |
| D4 | History storage | **Daily rollup store** (`~/.claude/usage-daily.jsonl`, one row/day) + keep the existing fine-grained utilization point-samples. |
| D5 | Backfill | **Yes** — one-time, local, memory-safe scan of `~/.claude/projects/*.jsonl` to reconstruct daily history on first run. |
| D6 | Charts | **Custom-painted reusable chart toolkit** (extends existing QPainter style); no QtCharts dependency. |

---

## 4. Architecture

### 4.1 Repo structure (target)
```
Claude Widget/                      (= arora-vaibhav/claude-usage-widget-windows)
  pyproject.toml                    # name, deps (PySide6), console_scripts, build
  LICENSE                           # upstream MIT (retained)
  NOTICE                            # attribution: upstream Burak + Windows port
  README.md                         # updated for full-source workflow
  setup.ps1                         # installs from LOCAL source (uv tool install .)
  launcher/claude-usage-launcher.vbs
  src/claude_usage/                 # vendored v0.6.6 source + merged patches
    ...all ~25 modules...
    dashboard/                      # NEW: Dashboard window package
      window.py                     # QMainWindow shell + navigation
      charts.py                     # reusable custom chart toolkit
      pages/                        # history, breakdowns, settings pages
    daily.py                        # NEW: daily rollup store (read/write/backfill)
  tests/
    unit/                           # pure-logic tests (history, daily, charts-as-image, pricing)
    ...
  docs/superpowers/specs/           # this spec + future specs
```
Install for daily use: `uv tool install .` (or `uv tool install -e .` while developing).
`setup.ps1` switches from "install PyPI + copy patches" to "install this repo + install
launcher + (optional) autostart".

### 4.2 Data model

**Existing (unchanged):** `~/.claude/usage-history.jsonl` — point samples
`{ts, session, weekly}` every refresh. Drives fine-grained "last 5h / 7d" utilization
curves. Pruned to 90 days. (Already implemented in `history.py` + `collector.py`.)

**New:** `~/.claude/usage-daily.jsonl` — one JSON object per calendar day (UTC), e.g.
(all values synthetic):
```json
{
  "date": "2026-05-29",
  "messages": 142,
  "sessions": 7,
  "tokens": {"input": 1200000, "output": 380000, "cache_read": 5400000, "cache_creation": 210000},
  "cost": 12.47,
  "cache_savings": 8.10,
  "by_model": {"claude-opus-4-8": {"input": 900000, "output": 250000}},
  "by_project": {"Claude Widget": 180000},
  "peak_session_util": 0.62,
  "peak_weekly_util": 0.48,
  "schema": 1
}
```
- Rationale for JSONL daily rollup (not SQLite): one row/day (~365/yr) is tiny;
  append/rewrite-today is trivial; human-inspectable; consistent with the existing
  history file; trivially backfillable; no new dependency. SQLite is the documented
  fallback if query needs outgrow this.
- **Write path:** each refresh, `collect_all` computes today's totals (already does) →
  `daily.upsert_today(...)` overwrites today's row (idempotent). Older rows are immutable.
- **Backfill path:** `daily.backfill(claude_dir)` scans all transcripts once, buckets
  per-message tokens/cost/model/project by UTC day, and writes rows for days not already
  present. Runs on first launch after upgrade (guarded by a marker), memory-safe
  (reuses the bounded-scan patterns from `cache_analyzer`/`ticker`), idempotent.
- **Cost** uses the existing `pricing.calculate_stats_cost`. Messages/sessions per day
  come from `history.jsonl` (has `timestamp` + `sessionId`).

> Honesty note: utilization % is API-sourced and **cannot** be backfilled (the API only
> reports *current* utilization). So historical `peak_*_util` exists only from samples we
> already recorded (good 90-day depth). Tokens/cost/messages/model/project **can** be
> backfilled as far back as transcripts are retained.

### 4.3 App shape
- **OSD widget** (`widget.py` / `overlay.py`): unchanged role. Gains a clean entry point
  to open the Dashboard (left-click → Dashboard; or right-click menu → "Open Dashboard").
- **Dashboard window** (`dashboard/`): a `QMainWindow` with a left nav and pages:
  - **Overview** — today/week at a glance (cost, tokens, messages, utilization gauges).
  - **History** — line/area charts: utilization over time, cost/day, tokens/day,
    messages/day; range selector (7d / 30d / 90d / all).
  - **Breakdowns** — per-model and per-project bar charts + tables (today / week / range).
  - **Settings** — the config currently buried in the right-click menu, surfaced as a
    real settings page (limits, refresh, theme, opacity, autostart, notifications, API).
- The Dashboard reads the same `UsageStats` + the daily store; no new data sources.

### 4.4 Chart toolkit (`dashboard/charts.py`)
A small set of `QWidget`s painted with `QPainter`, sharing one layout/axis/tooltip core:
- `LineChart` / `AreaChart` (time series: utilization, cost, tokens, messages)
- `BarChart` (per-day volumes; per-model / per-project breakdowns)
- Shared helpers: nice-number axis ticks, gridlines, hover hit-testing → tooltip,
  theme-aware colors (pull from `themes.py`), empty/loading states.
- **Testable:** each chart renders to a `QImage` off-screen; unit tests assert on
  size/non-empty/region rather than pixels, plus the pure axis/scaling math is tested
  directly.

---

## 5. Phases & acceptance criteria

### Phase 0 — Foundation
**Deliverables:** working dir becomes the continued git repo; full v0.6.6 source vendored
into `src/claude_usage/`; the 5 patches merged into source; `pyproject.toml`, `LICENSE`,
`NOTICE`, updated `README.md`; `setup.ps1` rewritten to install from local source; tests
scaffold + runnable `pytest`; this spec committed.
**Acceptance:** `uv tool install .` from the repo produces a working `claude-usage` that
launches the OSD; `claude-usage --version` prints the version; `pytest` runs green;
`git log` shows continuity with the existing repo; `setup.ps1` no longer references PyPI.

### Phase 1 — Stabilize
**Deliverables:** diagnose the recording stall (systematic-debugging) and fix root cause;
real rotating log file (e.g. `~/.claude/claude-usage.log`); single-instance lock;
normalize `claude_dir` slashes; harden the refresh loop so one bad transcript/exception
can't kill recording; **autostart** option (Startup shortcut / registry Run, opt-in).
**Acceptance:** widget records a new sample every refresh across an extended run; killing
and relaunching is single-instance; log file captures exceptions; after a simulated
reboot (autostart enabled) the widget is running and recording; numbers match a
hand-computed spot check from transcripts.

### Phase 2 — Usage history
**Deliverables:** `daily.py` store (upsert + load + prune); backfill from transcripts;
collector wired to upsert today each refresh; the four history dimensions available to
the UI; export extended to the daily store.
**Acceptance:** after backfill, `usage-daily.jsonl` has one row per active day with
plausible tokens/cost/model/project; values reconcile with the existing today/week
numbers for today; backfill is idempotent (re-run adds nothing) and memory-safe on a
large `projects/` dir.

### Phase 3 — UI modernization
**Deliverables:** Dashboard window + chart toolkit + the four pages; cohesive restyle of
OSD + Dashboard; tray icon; smooth open/close. Designed with mockups (visual companion).
**Acceptance:** Dashboard opens from the widget, renders all four history dimensions +
breakdowns from real data, respects the active theme, and is readable/uncramped; OSD and
Dashboard share one visual language; verified by running the app (screenshots).

---

## 6. Risks & mitigations
- **Heavy collection causing stalls/jank** → Phase 1 hardens the loop + adds logging;
  consider moving collection to a worker thread if diagnosis shows main-thread blocking.
- **Backfill on huge `projects/` dirs** → reuse bounded/memory-safe scan patterns
  (budgets, `MemoryError` guards) already proven in `cache_analyzer.py`/`ticker.py`.
- **Cross-platform regressions** from Windows-focused fixes → keep changes
  platform-conditional; keep pure logic in tested modules.
- **Theme-matching for charts** → charts pull colors from `themes.py`; tested against
  multiple palettes.
- **Repo-continuity mechanics** (continuing the existing remote while restructuring) →
  handled explicitly in the Phase 0 plan; preserve `.git`, launcher, setup, README.

## 7. Testing strategy
- Pure-logic unit tests: `history.aggregate`, `daily` upsert/backfill/bucketing,
  `pricing`, chart axis/scaling math, `config` slash-normalization.
- Render tests: charts → off-screen `QImage`, assert non-empty / dimensions / regions.
- Manual/verify: run the app on Windows, exercise Dashboard, screenshot (Phase 3).

## 8. Out of scope / future
- PyPI publication of the fork; Linux/macOS packaging; richer analytics (forecasting UI);
  syncing history across machines.
