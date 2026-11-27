"""Limit alerts: a notification the first time a limit crosses a threshold in its window.

:class:`CrossingDetector` holds the pure crossing logic. :class:`UsageNotifier`
feeds it the snapshot ``now`` dict and hands each alert to a sender, which the
controller wires to a tray balloon.
"""

from __future__ import annotations

from typing import Callable, Iterable

REARM = 0.02  # a threshold re-arms only after a dip this far below it (a window reset), not on jitter


class CrossingDetector:
    """Upward threshold crossings per scope.

    A threshold fires only on the step that crosses it, so it fires once per
    window. It re-arms when the value falls more than REARM below it, as at a
    reset; a reading that wobbles across it (rounding, a source switch) does not.
    """

    def __init__(self, thresholds: Iterable[float]):
        self.thresholds = sorted(t for t in thresholds if 0.0 < t <= 1.0)
        self._last: dict[str, float] = {}

    def check(self, scope: str, util: float) -> list[float]:
        """Record *util* for *scope*; return the thresholds it just crossed.

        The first value of a scope is only a baseline: starting the app at 80%
        must not alert for a crossing that happened earlier.
        """
        prev = self._last.get(scope)
        # The level a threshold must be above to fire: the value itself, except that a dip
        # keeps it up to REARM above the value, so thresholds just above the dip stay fired.
        self._last[scope] = util if prev is None else max(util, min(prev, util + REARM))
        if prev is None:
            return []
        return [t for t in self.thresholds if prev < t <= util]


class UsageNotifier:
    """Alerts when the weekly limit, the 5-hour limit or an active scoped limit (Fable on
    Ultra) crosses a configured threshold. A limit the plan does not report never alerts."""

    LABELS = {"weekly": "Weekly", "session": "5-hour"}

    def __init__(self, thresholds: Iterable[float], send: Callable[[str, str], None], enabled: bool = True):
        self.enabled = enabled
        self.detector = CrossingDetector(thresholds)
        self._send = send

    def set_thresholds(self, thresholds: Iterable[float]) -> None:
        # Keeps the per-scope baseline: a new threshold fires when a meter next rises across it,
        # never for a level the meter had already passed.
        self.detector.thresholds = CrossingDetector(thresholds).thresholds

    def check_now(self, now: dict, scoped: Iterable[dict] = ()) -> None:
        """*scoped*: the active scoped limits, {name, pct}, from the limits fetch."""
        meters = [(label, now.get(scope), scope) for scope, label in self.LABELS.items()]
        meters += [(x["name"], x, "scoped:" + x["name"]) for x in scoped]
        for label, meter, scope in meters:
            if not meter:  # not reported (the 5-hour limit on Ultra)
                continue
            for t in self.detector.check(scope, meter["pct"]):
                if self.enabled:
                    forecast = f" {meter['forecast']}." if meter.get("forecast") else ""
                    self._send(f"{label} limit at {round(meter['pct'] * 100)}%",
                               f"Crossed the {round(t * 100)}% alert.{forecast}")
