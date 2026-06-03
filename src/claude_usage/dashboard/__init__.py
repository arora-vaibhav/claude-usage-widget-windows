"""Dashboard window package — the modern, full-size view of usage history.

The floating OSD stays the at-a-glance surface; this package provides a proper
window (opened from the widget) that charts the daily-rollup history
(:mod:`claude_usage.daily`). Charts are custom-painted (no QtCharts dependency)
so they stay on PySide6-Essentials and share the app's theme palette.
"""

from claude_usage.dashboard.unified import UnifiedWindow
from claude_usage.dashboard.window import DashboardWindow

__all__ = ["DashboardWindow", "UnifiedWindow"]
