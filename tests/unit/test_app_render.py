"""The app window: every page in light and dark loads offscreen from the
synthetic fixtures with zero QML warnings, survives the data states, and
wires range, project and settings back to the controller."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # the offscreen platform finds no fonts without this
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage.app import AppWindow  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from snapshot_contract import check, check_consistency  # noqa: E402

_app = QApplication.instance() or QApplication([])

FIXTURES = Path(__file__).parent.parent / "fixtures"
SNAPS = [p for p in (FIXTURES / "app_fixture.json", FIXTURES / "snapshot_sample.json") if p.exists()]
PAGES = ("overview", "sessions", "settings")
OUT = Path(tempfile.gettempdir()) / "claude_usage_app_render"


def pump(ms: int = 250) -> None:
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        _app.processEvents()
        time.sleep(0.005)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Harness:
    def __init__(self, snap: dict) -> None:
        self.snap = snap
        self.calls: list[tuple] = []
        self.settings: dict = {}
        self.win = AppWindow(self.provide, settings={"appearance": "light"}, on_setting=self.settings.__setitem__)
        self.win.update_snapshot(snap)
        self.win.show()
        self.win.window.resize(1280, 820)
        pump()

    def provide(self, range_key, project):
        self.calls.append((range_key, project))
        out = copy.deepcopy(self.snap)
        out["range"]["key"] = range_key
        out["project"] = project
        return out


@pytest.fixture(scope="module", params=SNAPS, ids=[p.stem for p in SNAPS])
def h(request):
    harness = Harness(load(request.param))
    harness.name = request.param.stem
    yield harness
    harness.win.prepare_quit()
    harness.win.window.close()
    pump(50)


def test_fixtures_follow_the_contract():
    for p in SNAPS:
        snap = load(p)
        check(snap)
        check_consistency(snap)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_warnings(h, page, dark):
    h.win.set_dark(dark)
    h.win.backend.pageRequested.emit(page)
    pump()
    img = h.win.window.grabWindow()
    OUT.mkdir(exist_ok=True)
    img.save(str(OUT / f"{h.name}_{page}_{'dark' if dark else 'light'}.png"))
    assert img.width() >= 1280 and img.height() >= 800
    # A blank or half-loaded window has a handful of colours; a page has hundreds.
    colours = {img.pixel(x, y) for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)}
    assert len(colours) > 40, f"{page} looks empty"
    assert h.win.warnings == []


STATES = {
    "signed_out": lambda s: (s["status"].update(state="signed_out"), s["now"].update(state="signed_out")),
    "indexing": lambda s: s["status"].update(indexing={"done": 120, "total": 900}),
    "stale": lambda s: s["status"].update(state="stale"),
    "error": lambda s: s["status"].update(state="error", message="Could not read the usage store."),
    "no_weekly": lambda s: s["limits"].update(weekly=None, past_weeks=[], weeks_hit=0, weeks_counted=0),
    "with_5h": lambda s: s["limits"].update(session={"pct": 0.4, "resets_at": s["range"]["to"], "level": "normal"}),
    "empty": lambda s: (s.update(sessions=[], sessions_total=0, project="nothing-here"),
                        [g["values"][m].__setitem__(i, [0] * len(g["values"][m][i]))
                         for g in s["timeline"]["by"].values() for m in g["values"] for i in range(len(g["values"][m]))]),
}


@pytest.mark.parametrize("state", list(STATES))
def test_states_render_without_warnings(h, state):
    snap = copy.deepcopy(h.snap)
    STATES[state](snap)
    for page in ("overview", "sessions"):
        h.win.update_snapshot(snap)
        h.win.backend.pageRequested.emit(page)
        pump(120)
    h.win.update_snapshot(h.snap)
    pump(60)
    assert h.win.warnings == []


def test_range_and_project_go_through_the_provider(h):
    h.calls.clear()
    h.win.backend.setRange("30d")
    pump(120)
    h.win.backend.setProject("some-project")
    pump(120)
    assert h.calls[0] == ("30d", None)
    assert h.calls[-1] == ("30d", "some-project")
    assert h.settings.get("app_range") == "30d"
    assert h.win.backend.rangeKey == "30d"
    h.win.backend.setProject("")
    h.win.backend.setRange("7d")
    pump(120)
    h.win.update_snapshot(h.snap)


def test_background_provider_keeps_the_page_dimmed_until_data_arrives(h):
    pending = []
    real, h.win.backend._provider = h.win.backend._provider, lambda r, p: pending.append((r, p))
    try:
        h.win.backend.setRange("90d")
        pump(80)
        assert pending == [("90d", None)] and h.win.backend.busy
        h.win.update_snapshot(h.snap)
        assert not h.win.backend.busy
    finally:
        h.win.backend._provider = real
        h.win.backend.setRange("7d")
        pump(80)
        h.win.update_snapshot(h.snap)


def test_settings_reach_the_controller_and_appearance_applies(h):
    h.win.backend.setSetting("appearance", "dark")
    pump(50)
    assert h.settings["appearance"] == "dark"
    assert h.win.backend.dark is True
    h.win.backend.setSetting("appearance", "light")
    pump(50)
    assert h.win.backend.dark is False


def test_close_hides_and_remembers_geometry(h):
    h.win.window.close()
    pump(50)
    assert not h.win.is_visible()
    assert len(h.settings.get("app_geometry", [])) == 4
    h.win.show()
    pump(50)
    assert h.win.is_visible()
