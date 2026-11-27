"""Snapshot contract and semantics on the synthetic store (tests/fixtures/make_store.py)."""

import os
import random
import sys
from datetime import datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "fixtures"))
sys.path.insert(0, HERE)

import make_store  # noqa: E402
import snapshot_contract  # noqa: E402

from claude_usage import ingest, snapshot, store  # noqa: E402
from test_ingest import _iso, _ts, asst, prompt, write  # noqa: E402

NOW = make_store.NOW


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    conn, limits = make_store.build_store(str(tmp_path_factory.mktemp("store")))
    yield conn, limits
    conn.close()


@pytest.mark.parametrize("rng", list(snapshot.RANGES))
@pytest.mark.parametrize("project", [None, "acme-api"])
def test_contract_every_range_and_project(built, rng, project):
    conn, limits = built
    snap = snapshot.build(conn, rng, project, NOW, limits)
    snapshot_contract.check(snap)
    snapshot_contract.check_consistency(snap)
    assert snap["range"]["key"] == rng and snap["project"] == project
    if project:
        assert {s["project"] for s in snap["sessions"]} <= {project}


def test_project_filter_splits_the_total(built):
    conn, limits = built
    total = snapshot.build(conn, "30d", None, NOW, limits)["timeline"]["totals"]["cost"]
    parts = sum(snapshot.build(conn, "30d", p["name"], NOW, limits)["timeline"]["totals"]["cost"]
                for p in snapshot.build(conn, "30d", None, NOW, limits)["projects"])
    assert parts == pytest.approx(total, rel=1e-6)


def test_deltas_need_a_previous_window_inside_coverage(built):
    conn, limits = built
    cov = store.get_meta(conn, "coverage_from")
    first_ok = datetime.fromtimestamp(NOW).date() - timedelta(days=60)
    assert cov is not None and cov < first_ok.isoformat()  # 30d's previous window starts inside coverage
    for rng in ("24h", "7d", "30d"):
        assert all(k["delta"] is not None and k["prev"] is not None for k in snapshot.build(
            conn, rng, None, NOW, limits)["kpis"]), rng
    for rng in ("90d", "all"):  # 90d's previous window reaches back past coverage_from
        assert all(k["delta"] is None and k["prev"] is None for k in snapshot.build(
            conn, rng, None, NOW, limits)["kpis"]), rng


@pytest.mark.parametrize("rng", ["24h", "7d", "30d"])
def test_previous_window_has_the_same_length(built, rng):
    conn, limits = built
    snap = snapshot.build(conn, rng, None, NOW, limits)
    t0 = datetime.fromisoformat(snap["range"]["from"]).timestamp()
    # Recompute straight from calls over [t0 - (NOW - t0), t0); the window starts mid-hour.
    p0 = t0 - (NOW - t0)
    from claude_usage import pricing
    prev, tokens = 0.0, 0
    for m, sp, i, o, r, w5, w1, ws in conn.execute(
            "SELECT model, speed, input, output, cache_read, cache_write_5m, cache_write_1h, web_search "
            "FROM calls WHERE ts_ms >= ? AND ts_ms < ?", (int(p0 * 1000), int(t0 * 1000))):
        prev += pricing.call_cost(m, i, o, r, w5, w1, web_search=ws, speed=sp or None)
        tokens += i + o + r + w5 + w1
    kpi = {k["key"]: k for k in snap["kpis"]}
    assert kpi["cost"]["prev"] == pytest.approx(prev, abs=0.01) and kpi["tokens"]["prev"] == tokens


def test_colours_are_the_same_in_every_range(built):
    conn, limits = built
    colour = {}
    for rng in snapshot.RANGES:
        for grp in ("project", "model"):
            g = snapshot.build(conn, rng, None, NOW, limits)["timeline"]["by"][grp]
            for k, c in zip(g["keys"], g["colors"]):
                if k == "Other":
                    assert c == snapshot.OTHER
                elif k != "Legacy":
                    assert colour.setdefault((grp, k), c) == c, (rng, k)
    for grp in ("project", "model"):  # no more entities than palette slots here, so none shares one
        mine = [c for (g, _k), c in colour.items() if g == grp]
        assert len(mine) == len(set(mine)) and all(0 <= c < 8 for c in mine)


def _call(root, project, when, out, tag, model="claude-opus-5-5"):
    write(root, f"C--work-{project}/{tag}.jsonl",
          [asst(tag, _iso(when), out=out, sid=tag, model=model, cwd=rf"C:\work\{project}")])


def _drawn(conn, now):
    """(range, group) -> {key: colour} for every range."""
    out = {}
    for rng in snapshot.RANGES:
        snap = snapshot.build(conn, rng, None, now, None)
        for grp in ("project", "model"):
            g = snap["timeline"]["by"][grp]
            out[rng, grp] = {k: c for k, c in zip(g["keys"], g["colors"]) if k != "Other"}
    return out


def _same_everywhere(drawn):
    seen = {}
    for (rng, grp), keys in drawn.items():
        assert len(set(keys.values())) == len(keys), (rng, grp, keys)  # a chart never draws a colour twice
        for k, c in keys.items():
            assert seen.setdefault((grp, k), c) == c, (rng, grp, k)  # and an entity has one colour everywhere
    return seen


def test_colours_survive_rank_changes(tmp_path):
    root = str(tmp_path / "projects")
    now = datetime(2026, 11, 20, 15, 0)
    for i, (p, out, model) in enumerate([("alpha", 400_000, "claude-opus-5-5"), ("beta", 300_000, "claude-opus-5-5"),
                                         ("gamma", 200_000, "claude-sonnet-5-5")]):
        _call(root, p, now - timedelta(hours=2 + i), out, f"a{i}", model=model)
    conn = store.connect(str(tmp_path / "usage.db"))
    ing = ingest.Ingestor(conn, root, clock=lambda: now.timestamp())
    ing.index()
    before = _same_everywhere(_drawn(conn, now.timestamp()))
    assert [before["project", p] for p in ("alpha", "beta", "gamma")] == [0, 1, 2]  # first drawn, by cost
    assert [before["model", m] for m in ("Opus 5.5", "Sonnet 5.5")] == [0, 1]
    # beta and Sonnet now cost the most in every range, and two newcomers outspend the rest
    _call(root, "beta", now - timedelta(hours=1), 5_000_000, "b1", model="claude-sonnet-5-5")
    _call(root, "delta", now - timedelta(minutes=50), 3_000_000, "b2")
    _call(root, "epsilon", now - timedelta(minutes=40), 2_000_000, "b3", model="claude-sonnet-5-5")
    ing.index()
    after = _same_everywhere(_drawn(conn, now.timestamp()))
    assert {k: c for k, c in after.items() if k in before} == before
    assert (after["project", "delta"], after["project", "epsilon"]) == (3, 4)  # next free slots, costlier first
    # a reopened store reads the same slots back
    reopened = store.connect(str(tmp_path / "usage.db"))
    assert _same_everywhere(_drawn(reopened, now.timestamp())) == after
    reopened.close()


def test_more_projects_than_colours_share_slots_safely(tmp_path):
    rng = random.Random(5)
    root = str(tmp_path / "projects")
    now = datetime(2026, 11, 20, 15, 0)
    n = 0
    for i in range(16):  # projects busy in different stretches of the last 120 days
        start = rng.randint(0, 120)
        for back in range(start, max(start - 25, -1), -1):
            if rng.random() < 0.4:
                n += 1
                _call(root, f"proj-{i:02d}", now - timedelta(days=back, hours=rng.uniform(0.5, 9)),
                      rng.randint(10_000, 2_000_000), f"c{n:04d}")
    conn = store.connect(str(tmp_path / "usage.db"))
    ing = ingest.Ingestor(conn, root, clock=lambda: now.timestamp())
    ing.index()
    first = _same_everywhere(_drawn(conn, now.timestamp()))
    assert len([k for k in first if k[0] == "project"]) > 8  # more than the palette holds, so some share a slot
    for i in (3, 9, 14):  # idle projects come back and reshuffle the ranks
        n += 1
        _call(root, f"proj-{i:02d}", now - timedelta(hours=1 + i / 10), 4_000_000, f"c{n:04d}")
    ing.index()
    _same_everywhere(_drawn(conn, now.timestamp()))
    later = now + timedelta(days=9)
    _same_everywhere(_drawn(conn, later.timestamp()))


def test_sessions_are_capped_with_a_total(built, monkeypatch):
    conn, limits = built
    monkeypatch.setattr(snapshot, "SESSION_CAP", 5)
    snap = snapshot.build(conn, "30d", None, NOW, limits)
    assert len(snap["sessions"]) == 5 and snap["sessions_total"] > 5


def test_top_sessions_rank_every_session_in_range(built, monkeypatch):
    conn, limits = built
    every = snapshot.build(conn, "30d", None, NOW, limits)
    assert every["sessions_total"] == len(every["sessions"]) > 40
    costs = sorted((s["cost"] for s in every["sessions"]), reverse=True)
    recent = sorted(every["sessions"], key=lambda s: s["ended"])[-5:]
    assert sorted(s["cost"] for s in recent) != sorted(costs[:5])  # so a cap by recency would drop a top session
    monkeypatch.setattr(snapshot, "SESSION_CAP", 5)
    capped = snapshot.build(conn, "30d", None, NOW, limits)
    assert [s["cost"] for s in capped["top_sessions"]] == costs[:10]
    assert [s["cost"] for s in capped["sessions"]] == costs[:5]
    assert all(s["started"] and s["ended"] for s in capped["top_sessions"])


def test_all_range_uses_legacy_days_before_coverage(built):
    conn, limits = built
    snap = snapshot.build(conn, "all", None, NOW, limits)
    g = snap["timeline"]["by"]["project"]
    assert "Legacy" in g["keys"] and g["colors"][g["keys"].index("Legacy")] == snapshot.LEGACY
    assert snap["status"]["legacy_before"] == snap["status"]["coverage_from"]
    legacy = conn.execute("SELECT SUM(cost) FROM legacy_days WHERE quality='legacy' AND day < ?",
                          (snap["status"]["coverage_from"],)).fetchone()[0]
    assert sum(g["values"]["cost"][g["keys"].index("Legacy")]) == pytest.approx(legacy, abs=0.01)
    # The models card adds up to the Cost KPI: legacy days get their own entry.
    assert sum(m["cost"] for m in snap["models"]) == pytest.approx(snap["timeline"]["totals"]["cost"], abs=0.05)
    assert next(m for m in snap["models"] if m["id"] == "legacy")["color"] == snapshot.LEGACY
    # The overnight copy row in the synthetic legacy file is never shown.
    assert conn.execute("SELECT COUNT(*) FROM legacy_days WHERE quality='corrupt'").fetchone()[0] >= 1


def test_null_five_hour_never_shows_zero(built):
    conn, limits = built
    snap = snapshot.build(conn, "7d", None, NOW, limits)
    assert snap["limits"]["session"] is None and snap["now"]["session"] is None
    assert snap["now"]["primary"] == "weekly"
    assert snap["limits"]["session_note"].startswith("Your plan has not reported a 5-hour limit")
    assert snap["status"]["plan"] is None
    now = snapshot.now_state(conn, limits, NOW)
    snapshot_contract.check(now, snapshot_contract.NOW)
    assert now == snap["now"]


def test_ultra_has_no_five_hour_limit_and_a_returning_one_shows(built):
    conn, limits = built
    ultra = dict(limits, plan="Ultra")
    snap = snapshot.build(conn, "7d", None, NOW, ultra)
    assert snap["status"]["plan"] == "Ultra" and snap["now"]["primary"] == "weekly"
    assert snap["limits"]["session"] is None and snap["now"]["session"] is None
    assert snap["limits"]["session_note"] == "Ultra has no 5-hour limit, so your weekly limit is the one that counts."
    back = snapshot.build(conn, "7d", None, NOW, dict(ultra, five_hour={"pct": 0.3, "resets_at": NOW + 3600,
                                                                          "severity": None}))
    assert back["limits"]["session"]["pct"] == 0.3 and back["now"]["session"]["pct"] == 0.3
    assert back["limits"]["session_note"] is None


def test_five_hour_projected_to_run_out_first_is_primary(built):
    conn, limits = built
    lim = dict(limits, five_hour={"pct": 0.8, "resets_at": NOW + 2 * 3600, "severity": None})
    now = snapshot.now_state(conn, lim, NOW)
    assert now["primary"] == "session" and now["session"]["pct"] == 0.8
    assert now["session"]["forecast"].startswith("Limit ")


def test_pace_engine(built):
    conn, limits = built
    w = snapshot.build(conn, "7d", None, NOW, limits)["limits"]["weekly"]
    reset = limits["weekly"]["resets_at"]
    assert w["elapsed"] == pytest.approx((NOW - (reset - 7 * 86400)) / (7 * 86400), abs=1e-4)
    assert w["pace"] == pytest.approx(w["pct"] / w["elapsed"], rel=1e-2)
    assert w["daily_budget"] == pytest.approx((1 - w["pct"]) / ((reset - NOW) / 86400), rel=1e-3)
    assert w["usual"] is not None and w["usual_series"]
    assert w["series"] and all(0 <= p["pct"] <= 1 for p in w["series"])
    lim = snapshot.build(conn, "7d", None, NOW, limits)["limits"]
    assert lim["weeks_counted"] >= 10 and 0 <= lim["weeks_hit"] <= lim["weeks_counted"]
    assert lim["past_weeks"][-1]["current"] is True


def test_signed_out_and_stale_states(built):
    conn, limits = built
    out = snapshot.now_state(conn, {"error": "No credentials found. Run claude to sign in."}, NOW)
    assert out["state"] == "signed_out" and out["weekly"] is None
    stale = snapshot.now_state(conn, dict(limits, error="Rate limited by the usage API. Retrying shortly."), NOW)
    assert stale["state"] == "stale" and stale["weekly"] is not None
    assert snapshot.now_state(conn, None, NOW)["state"] == "loading"


def test_unknown_model_is_listed_as_unpriced(built):
    conn, limits = built
    models = snapshot.build(conn, "all", None, NOW, limits)["models"]
    nova = [m for m in models if m["priced"] == "unpriced"]
    assert nova and all(m["cost"] == 0 for m in nova)


def test_kpi_totals_match_the_timeline(built):
    conn, limits = built
    for rng in snapshot.RANGES:
        snap = snapshot.build(conn, rng, None, NOW, limits)
        kpi = {k["key"]: k for k in snap["kpis"]}
        assert kpi["cost"]["value"] == pytest.approx(snap["timeline"]["totals"]["cost"], abs=0.01)
        assert sum(kpi["cost"]["series"]) == pytest.approx(kpi["cost"]["value"], abs=0.05)
        assert sum(sum(s) for s in snap["timeline"]["by"]["model"]["values"]["cost"]) == pytest.approx(
            snap["timeline"]["totals"]["cost"], abs=0.05)


def test_weekly_meter_without_a_reset_time(built):
    conn, limits = built
    lim = dict(limits, weekly={"pct": 0.5, "resets_at": None, "severity": None}, window_started_at=None)
    snap = snapshot.build(conn, "7d", None, NOW, lim)
    snapshot_contract.check(snap)
    assert snap["now"]["weekly"]["pct"] == 0.5 and snap["now"]["weekly"]["forecast"] is None
    assert snap["limits"]["weekly"]["pct"] == 0.5 and snap["limits"]["weekly"]["pace"] is None
    # The week in progress is not listed as a finished week; the finished ones still are.
    cur = limits["weekly"]["resets_at"] - 7 * 86400
    weeks = snap["limits"]["past_weeks"]
    assert weeks and all(datetime.fromisoformat(w["start"]).timestamp() < cur for w in weeks)
    assert snap["limits"]["weeks_counted"] == snapshot.build(conn, "7d", None, NOW, limits)["limits"]["weeks_counted"]


def _now_of(minutes):
    return datetime.fromisoformat(_ts(minutes).replace("Z", "+00:00")).timestamp()


def test_caches_follow_each_store_and_every_rollup(tmp_path):
    now = _now_of(30)
    stores = {}
    for name in ("alpha", "beta"):  # same shape, different content
        root = str(tmp_path / name)
        write(root, "C--work-x/s1.jsonl", [asst("m1", _ts(0), out=1000, cwd=rf"C:\work\{name}")])
        write(root, "C--work-x/s1/subagents/agent-a1.jsonl", [asst("m2", _ts(1), agentId="a1")])
        conn = store.connect(str(tmp_path / (name + ".db")))
        ing = ingest.Ingestor(conn, root, clock=lambda: now)
        ing.index()
        stores[name] = root, conn, ing
        assert [p["name"] for p in snapshot.build(conn, "7d", None, now, None)["projects"]] == [name]
    root, conn, ing = stores["beta"]
    write(root, "C--work-x/s1/subagents/agent-a2.jsonl", [asst("m3", _ts(2), agentId="a2")])
    ing.tail()  # rebuilds the same rollup rows with new sums
    assert snapshot.build(conn, "7d", None, now, None)["sessions"][0]["subagents"] == 2
    write(root, "C--work-y/s2.jsonl", [asst("m4", _ts(3), out=900_000, sid="s2", cwd=r"C:\work\gamma")])
    ing.tail()
    assert {p["name"]: p["color"] for p in snapshot.build(conn, "7d", None, now, None)["projects"]} == \
        {"gamma": 1, "beta": 0}  # beta keeps the slot it had before gamma outspent it


def test_session_started_before_the_range_shows_its_real_start(tmp_path):
    root = str(tmp_path / "projects")
    first = datetime(2026, 11, 10, 9, 0)
    write(root, "C--work-x/s1.jsonl", [asst("m1", _iso(first)), asst("m2", _iso(datetime(2026, 11, 20, 14, 0)))])
    conn = store.connect(str(tmp_path / "usage.db"))
    now = datetime(2026, 11, 20, 15, 0).timestamp()
    ingest.Ingestor(conn, root, clock=lambda: now).index()
    (s,) = snapshot.build(conn, "7d", None, now, None)["sessions"]
    assert datetime.fromisoformat(s["started"]).replace(tzinfo=None) == first


def test_in_memory_stores_are_not_served_from_the_cache(tmp_path):
    now = _now_of(30)
    for name in ("alpha", "beta", "gamma", "delta"):  # a closed store's id(conn) can come back
        root = str(tmp_path / name)
        write(root, "C--work-x/s1.jsonl", [asst("m1", _ts(0), cwd=rf"C:\work\{name}")])
        conn = store.connect(":memory:")
        ingest.Ingestor(conn, root, clock=lambda: now).index()
        assert [p["name"] for p in snapshot.build(conn, "7d", None, now, None)["projects"]] == [name]
        conn.close()


def test_a_prompt_without_a_new_call_marks_the_session_interactive(tmp_path):
    root = str(tmp_path / "projects")
    path = write(root, "C--work-x/s1.jsonl", [asst("m1", _ts(0))])
    conn = store.connect(str(tmp_path / "usage.db"))
    now = _now_of(30)
    ing = ingest.Ingestor(conn, root, clock=lambda: now)
    ing.index()
    assert snapshot.build(conn, "7d", None, now, None)["sessions"][0]["interactive"] is False
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(prompt("p1", _ts(1)) + "\n")
    ing.tail()  # a turn and no call: the rollups are not rebuilt
    assert snapshot.build(conn, "7d", None, now, None)["sessions"][0]["interactive"] is True


def test_without_a_reset_time_the_week_in_progress_is_not_a_past_week(tmp_path):
    conn = store.connect(str(tmp_path / "usage.db"))
    for h in range(7 * 24):  # a finished week that peaked near 66%
        store.add_sample(conn, NOW - (13 * 24 - h) * 3600, round(0.1 + h / 300, 3), None)
    for h in range(6 * 24):  # then a reset and six days of the week in progress
        store.add_sample(conn, NOW - (6 * 24 - h) * 3600, round(0.05 + h / 200, 3), None)
    lim = {"weekly": {"pct": 0.8, "resets_at": None, "severity": None}, "five_hour": None, "fetched_at": NOW}
    snap = snapshot.build(conn, "7d", None, NOW, lim)
    snapshot_contract.check(snap)
    assert [(round(w["peak"], 2), w["current"]) for w in snap["limits"]["past_weeks"]] == [(0.66, False)]
