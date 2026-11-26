"""Snapshot contract and semantics on the synthetic store (tests/fixtures/make_store.py)."""

import os
import sys
from datetime import datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "fixtures"))
sys.path.insert(0, HERE)

import make_store  # noqa: E402
import snapshot_contract  # noqa: E402

from claude_usage import snapshot, store  # noqa: E402

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


def test_colours_follow_all_time_rank_not_the_range(built):
    conn, limits = built
    colour = {}
    for rng in snapshot.RANGES:
        g = snapshot.build(conn, rng, None, NOW, limits)["timeline"]["by"]["project"]
        for k, c in zip(g["keys"], g["colors"]):
            if k == "Other":
                assert c == snapshot.OTHER
            elif k != "Legacy":
                assert colour.setdefault(k, c) == c, (rng, k)
    assert sorted(colour.values()) == sorted(set(colour.values()))


def test_sessions_are_capped_with_a_total(built, monkeypatch):
    conn, limits = built
    monkeypatch.setattr(snapshot, "SESSION_CAP", 5)
    snap = snapshot.build(conn, "30d", None, NOW, limits)
    assert len(snap["sessions"]) == 5 and snap["sessions_total"] > 5


def test_all_range_uses_legacy_days_before_coverage(built):
    conn, limits = built
    snap = snapshot.build(conn, "all", None, NOW, limits)
    g = snap["timeline"]["by"]["project"]
    assert "Legacy" in g["keys"] and g["colors"][g["keys"].index("Legacy")] == snapshot.LEGACY
    assert snap["status"]["legacy_before"] == snap["status"]["coverage_from"]
    legacy = conn.execute("SELECT SUM(cost) FROM legacy_days WHERE quality='legacy' AND day < ?",
                          (snap["status"]["coverage_from"],)).fetchone()[0]
    assert sum(g["values"]["cost"][g["keys"].index("Legacy")]) == pytest.approx(legacy, abs=0.01)
    # The overnight copy row in the synthetic legacy file is never shown.
    assert conn.execute("SELECT COUNT(*) FROM legacy_days WHERE quality='corrupt'").fetchone()[0] >= 1


def test_null_five_hour_never_shows_zero(built):
    conn, limits = built
    snap = snapshot.build(conn, "7d", None, NOW, limits)
    assert snap["limits"]["session"] is None and snap["now"]["session"] is None
    assert snap["now"]["primary"] == "weekly"
    assert snap["limits"]["session_note"].startswith("Your plan has not reported a 5-hour limit")
    now = snapshot.now_state(conn, limits, NOW)
    snapshot_contract.check(now, snapshot_contract.NOW)
    assert now == snap["now"]


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
    out = snapshot.now_state(conn, {"error": "No credentials found -- run 'claude' to log in"}, NOW)
    assert out["state"] == "signed_out" and out["weekly"] is None
    stale = snapshot.now_state(conn, dict(limits, error="Rate limited -- try again shortly"), NOW)
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
