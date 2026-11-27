"""Design tokens: the one source of colour for every surface.

The OSD painter reads these through :func:`palette`; the Qt Quick app gets the
same dicts as a context property. Values were checked for WCAG contrast in
both modes (text pairs 4.5:1 or better, meters 3:1 against their track).
"""

from __future__ import annotations

LIGHT: dict[str, str] = {
    "bg": "#F5F5F3",
    "sidebar": "#EFEEEA",
    "surface": "#FFFFFF",
    "surface_alt": "#F2F1ED",
    "osd_bg": "#FFFFFF",
    "line": "#E6E4DF",
    "line_strong": "#D3D1CA",
    "track": "#ECEBE6",
    "text": "#1D1C1A",
    "text_muted": "#57554F",
    "text_faint": "#6E6C66",
    "accent": "#2A6FD0",
    "on_accent": "#FFFFFF",
    "accent_text": "#1F5FB8",
    "ok": "#1E7B34",
    "warn": "#8A5A00",
    "bad": "#B4401B",
    "warn_fill": "#B87A00",
    "bad_fill": "#D03B3B",
    "ok_bg": "#E8F5E9",
    "warn_bg": "#FBF2D7",
    "bad_bg": "#FCEBE4",
    "tooltip_bg": "#1D1C1A",
    "tooltip_text": "#FFFFFF",
    "brand": "#C15F3C",
}

DARK: dict[str, str] = {
    "bg": "#0D0D0D",
    "sidebar": "#111110",
    "surface": "#1A1A19",
    "surface_alt": "#242422",
    "osd_bg": "#202020",  # the Windows 11 dark taskbar, so a pill parked on it does not read as a hole
    "line": "#2C2C2A",
    "line_strong": "#3A3A37",
    "track": "#2A2A28",
    "text": "#F3F2EE",
    "text_muted": "#B4B2AB",
    "text_faint": "#8F8D86",
    "accent": "#5B9BEF",
    "on_accent": "#0D0D0D",
    "accent_text": "#7EB0F2",
    "ok": "#4CC764",
    "warn": "#F1C96B",
    "bad": "#FF7A6B",
    "warn_fill": "#E0A526",
    "bad_fill": "#E66767",
    "ok_bg": "#13261A",
    "warn_bg": "#2A2412",
    "bad_bg": "#2A1513",
    "tooltip_bg": "#2A2A28",
    "tooltip_text": "#F3F2EE",
    "brand": "#D97757",
}

# Categorical chart colours. snapshot.py gives each project and model a slot
# that it keeps across ranges and days (stored in the store's meta); "Other"
# uses OTHER. Checked with the dataviz validator: adjacent pairs and the first five
# as all pairs stay apart under protan and deutan (OKLab dE >= 9) and in normal
# vision (>= 15), light slots are 3:1 or better on white, dark slots sit in the
# dark lightness band. One green (teal), and no slot reads as bad_fill or
# warn_fill. Order: blue, orange, sky, pink, bronze, violet, teal, orchid.
SERIES_LIGHT = ["#3461C9", "#E26B1B", "#2E9FC9", "#C94B87", "#895709", "#9267CA", "#148C66", "#882B7F"]
SERIES_DARK = ["#466EDD", "#E27100", "#27A5C0", "#D14184", "#90601F", "#A67CE0", "#31AC81", "#A044AE"]
OTHER_LIGHT = "#858379"
OTHER_DARK = "#6B6A66"

# Usage severity thresholds (fractions). Shared by the OSD, the app and the
# notifier; config "notify_thresholds" overrides the first two.
CAUTION = 0.75
CRITICAL = 0.90


def palette(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT


def series(dark: bool) -> list[str]:
    return SERIES_DARK if dark else SERIES_LIGHT


def level(pct: float | None, caution: float = CAUTION, critical: float = CRITICAL) -> str:
    """Severity name for a 0..1 utilization: none, normal, caution, critical or limited."""
    if pct is None:
        return "none"
    if pct >= 1.0:
        return "limited"
    if pct >= critical:
        return "critical"
    if pct >= caution:
        return "caution"
    return "normal"
