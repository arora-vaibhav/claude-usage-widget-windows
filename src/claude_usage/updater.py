"""Check this fork's GitHub Releases for a version newer than the running one.

Stdlib networking only; callers run it off the GUI thread.
"""

from __future__ import annotations

import json
import re
from urllib.request import Request, urlopen

RELEASE_URL = "https://api.github.com/repos/arora-vaibhav/claude-usage-widget-windows/releases/latest"
UPGRADE_HINT = "Run git pull in your clone, then setup.ps1 again."

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def _parse(version: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(version.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _semver_greater(latest: str, current: str) -> bool:
    """True iff *latest* is a strictly greater semver than *current*."""
    a, b = _parse(latest), _parse(current)
    return a is not None and b is not None and a > b


def check_latest_version(current: str) -> tuple[str | None, bool]:
    """``(latest_tag, newer_than_current)``; ``(None, False)`` on any network or parse error."""
    req = Request(RELEASE_URL, headers={"User-Agent": "claude-usage-widget"})
    try:
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read(1 << 20).decode("utf-8", errors="replace"))
    except Exception:
        return None, False
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str):
        return None, False
    return tag, _semver_greater(tag, current)
