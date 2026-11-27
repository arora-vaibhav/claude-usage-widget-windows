"""Build a synthetic usage store: fake transcripts, legacy files and utilization samples.

Everything here is invented (project names, ids, titles, numbers). The store is
built through the real ingest and legacy import, so tests exercise the whole path.

    python tests/fixtures/make_store.py          # rewrites tests/fixtures/snapshot_sample.json
    from make_store import build_store, NOW      # in tests: conn, limits = build_store(tmp_dir)
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "src"))

from claude_usage import ingest, snapshot, store  # noqa: E402

NOW = datetime(2026, 11, 26, 14, 20).timestamp()  # local time, so day boundaries follow the machine's zone
LEDGER_DAYS = 110  # transcripts cover the last 110 days
LEGACY_DAYS = 30  # usage-daily rows before that
PROJECTS = ["acme-api", "web-console", "data-pipeline", "docs-site", "mobile-app", "infra", "ml-eval", "billing"]
WEIGHTS = [30, 18, 14, 10, 9, 8, 6, 5]
MODELS = [("claude-opus-5-5", 60), ("claude-sonnet-5-5", 24), ("claude-haiku-5-5", 10), ("claude-fable-5-2", 5),
          ("claude-nova-1", 1)]  # the last one is not in the price table
TITLES = ["Fix login redirect", "Add export button", "Speed up nightly job", "Refactor settings page",
          "Write release notes", "Investigate flaky test", "Migrate config loader", "Tune retry policy"]
RESET_HOUR_UTC = 1  # weekly limit resets Tuesdays 01:00 UTC


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ts * 1000) % 1000:03d}Z"


def _line(rec: dict) -> str:
    return json.dumps(rec, separators=(",", ":"))


def _assistant(mid, ts, sid, cwd, model, u, agent=None, entry="cli"):
    rec = {"parentUuid": None, "isSidechain": agent is not None, "type": "assistant",
           "message": {"id": mid, "model": model, "role": "assistant", "content": [], "usage": u},
           "uuid": f"a-{mid}-{u['output_tokens']}", "timestamp": _iso(ts), "sessionId": sid, "cwd": cwd,
           "entrypoint": entry, "version": "9.9.9"}
    if agent:
        rec["agentId"] = agent
    return _line(rec)


def _usage(rng, model, sub):
    long = model == "claude-haiku-5-5" and rng.random() < 0.3
    cr = rng.randint(120_000, 180_000) if long else rng.randint(5_000, 90_000)
    w = rng.randint(200, 8_000)
    return {"input_tokens": rng.randint(1, 12), "output_tokens": rng.randint(40, 2500),
            "cache_read_input_tokens": cr, "cache_creation_input_tokens": w,
            "cache_creation": {"ephemeral_5m_input_tokens": w if sub else 0, "ephemeral_1h_input_tokens": 0 if sub else w},
            "service_tier": "standard", "speed": "standard"}


def _pick(rng, pairs):
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]


def write_transcripts(root: str, seed: int = 7) -> None:
    rng = random.Random(seed)
    n_msg = 0
    copies = []
    for day_back in range(LEDGER_DAYS - 1, -1, -1):
        day0 = datetime.fromtimestamp(NOW).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=day_back)
        for _ in range(rng.randint(2, 6)):
            proj = rng.choices(PROJECTS, weights=WEIGHTS)[0]
            cwd = rf"C:\work\{proj}" + (r"\.worktrees\feat-x" if rng.random() < 0.15 else "")
            slug = "C--work-" + proj
            sid = "%08x-%04x-%04x" % (rng.getrandbits(32), rng.getrandbits(16), rng.getrandbits(16))
            auto = rng.random() < 0.2
            entry = "sdk-cli" if auto else rng.choice(["cli", "claude-desktop"])
            t = day0.timestamp() + rng.uniform(7, 22) * 3600
            if t > NOW - 120:
                continue
            main, subs = [], {}
            if not auto and rng.random() < 0.6:
                main.append(_line({"type": "ai-title", "aiTitle": rng.choice(TITLES), "sessionId": sid}))
            for p in range(rng.randint(1, 3) if auto else rng.randint(3, 10)):
                if not auto:
                    main.append(_line({"parentUuid": None, "isSidechain": False, "type": "user",
                                       "message": {"role": "user", "content": "please continue"},
                                       "uuid": f"p-{sid}-{p}", "timestamp": _iso(t), "sessionId": sid, "cwd": cwd,
                                       "entrypoint": entry}))
                start = t
                for _c in range(rng.randint(2, 8)):
                    t += rng.uniform(5, 90)
                    if t > NOW - 60:
                        break
                    n_msg += 1
                    mid = f"msg_{n_msg:08d}"
                    model = _pick(rng, MODELS)
                    u = _usage(rng, model, False)
                    # Streamed lines: early ones carry a partial output count (rule A).
                    for k in range(rng.randint(1, 3), 0, -1):
                        part = dict(u, output_tokens=max(1, u["output_tokens"] // (k * 3)) if k > 1 else u["output_tokens"])
                        main.append(_assistant(mid, t, sid, cwd, model, part, entry=entry))
                    if rng.random() < 0.01:
                        copies.append((slug, mid, t, model, u))
                if rng.random() < 0.25:
                    aid = "a%015x" % rng.getrandbits(60)
                    lines = subs.setdefault(aid, [])
                    st = start
                    for _c in range(rng.randint(3, 12)):
                        st += rng.uniform(2, 30)
                        if st > NOW - 60:
                            break
                        n_msg += 1
                        model = rng.choice(["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"])
                        lines.append(_assistant(f"msg_{n_msg:08d}", st, sid, cwd, model, _usage(rng, model, True),
                                                agent=aid, entry=entry))
                if not auto and rng.random() < 0.3:
                    main.append(_line({"parentUuid": None, "isSidechain": False, "type": "system",
                                       "subtype": "turn_duration", "durationMs": int((t - start) * 1000) + 4000,
                                       "messageCount": 4, "timestamp": _iso(t + 2), "uuid": f"d-{sid}-{p}",
                                       "sessionId": sid, "cwd": cwd, "entrypoint": entry}))
                t += rng.uniform(60, 1500)
            _write(os.path.join(root, slug, sid + ".jsonl"), main)
            for aid, lines in subs.items():
                _write(os.path.join(root, slug, sid, "subagents", f"agent-{aid}.jsonl"), lines)
    # A resumed session repeats a few messages from earlier sessions: they must count once.
    resumed = [_assistant(mid, t, "resumed-0001", rf"C:\work\{slug[8:]}", model, u) for slug, mid, t, model, u in copies]
    _write(os.path.join(root, "C--work-acme-api", "resumed-0001.jsonl"), resumed)


def _write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")


def _reset_after(ts: float) -> float:
    d = datetime.fromtimestamp(ts, timezone.utc).replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)
    d += timedelta(days=(1 - d.weekday()) % 7)  # Tuesday
    while d.timestamp() <= ts:
        d += timedelta(days=7)
    return d.timestamp()


def write_legacy(claude_dir: str, seed: int = 11) -> None:
    rng = random.Random(seed)
    today = datetime.fromtimestamp(NOW).date()
    rows, prev = [], None
    for back in range(LEDGER_DAYS + LEGACY_DAYS, LEDGER_DAYS - 3, -1):
        d = (today - timedelta(days=back)).isoformat()
        cost = round(rng.uniform(60, 240), 4) if back > LEDGER_DAYS else round(rng.uniform(5, 20), 4)
        msgs = rng.randint(200, 900)
        if back == LEDGER_DAYS + 10 and prev:  # the overnight copy bug: yesterday's row repeated
            cost, msgs = prev
        rows.append({"date": d, "messages": msgs, "sessions": 0, "cost": cost,
                     "tokens": {"input": 100, "output": msgs * 900, "cache_read": msgs * 40_000, "cache_creation": 0},
                     "schema": 1})
        prev = (cost, msgs)
    with open(os.path.join(claude_dir, "usage-daily.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
    # Weekly utilization every 30 minutes for 100 days; 5h stopped being reported 27 days ago.
    lines = []
    t = NOW - 100 * 86400
    while t <= NOW:
        reset = _reset_after(t)
        frac = 1 - (reset - t) / (7 * 86400)
        week_no = int(reset // (7 * 86400))
        peak = 0.75 + 0.35 * ((week_no * 7919) % 10) / 10
        weekly = round(min(1.0, peak * frac ** 0.9), 2)
        session = round(rng.uniform(0.05, 0.6), 2) if t < NOW - 27 * 86400 else 0.0
        lines.append(json.dumps({"ts": t, "session": session, "weekly": weekly}))
        t += 1800
    with open(os.path.join(claude_dir, "usage-history.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def synthetic_limits(conn) -> dict:
    """A fetch_rate_limits()-shaped dict consistent with the stored samples."""
    reset = _reset_after(NOW)
    weekly = conn.execute("SELECT weekly FROM samples ORDER BY ts DESC LIMIT 1").fetchone()[0]
    return {
        "source": "oauth", "fetched_at": int(NOW - 40), "plan": None,
        "weekly": {"pct": weekly, "resets_at": int(reset), "severity": "normal"},
        "five_hour": None,
        "limits": [
            {"kind": "weekly_all", "group": "weekly", "name": None, "pct": weekly, "severity": "normal",
             "resets_at": int(reset), "active": True, "allowance": None, "used_of_weekly": None},
            {"kind": "weekly_scoped", "group": "weekly", "name": "Fable", "pct": 0.0, "severity": "normal",
             "resets_at": int(reset), "active": False, "allowance": 0.5, "used_of_weekly": 0.0},
        ],
        "surfaces": [{"key": "claude_code", "name": "Claude Code", "pct": 0.96, "pct_of_limit": round(weekly * 0.96, 2)},
                     {"key": "chat", "name": "Chats", "pct": 0.04, "pct_of_limit": round(weekly * 0.04, 2)}],
        "window_started_at": int(reset - 7 * 86400),
        "extra_usage": {"enabled": False, "monthly_limit": 15.0, "used": 0.0, "currency": "CAD",
                        "reason": "out_of_credits", "spend_limit_reached": False},
        "status": None, "representative_claim": None,
    }


def build_store(base_dir: str):
    """Create transcripts, legacy files and the store under base_dir. Returns (conn, limits)."""
    claude_dir = os.path.join(base_dir, "claude")
    root = os.path.join(claude_dir, "projects")
    os.makedirs(root, exist_ok=True)
    write_transcripts(root)
    write_legacy(claude_dir)
    conn = store.connect(os.path.join(base_dir, "usage.db"))
    store.import_legacy(conn, claude_dir)
    ingest.Ingestor(conn, root, clock=lambda: NOW).index()
    return conn, synthetic_limits(conn)


if __name__ == "__main__":
    import tempfile

    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "unit"))
    import snapshot_contract

    with tempfile.TemporaryDirectory() as tmp:
        conn, limits = build_store(tmp)
        snap = snapshot.build(conn, "7d", None, NOW, limits, updated_at=NOW - 5)
        snapshot_contract.check(snap)
        snapshot_contract.check_consistency(snap)
        conn.close()
    out = os.path.join(HERE, "snapshot_sample.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1)
    print("wrote", out)
