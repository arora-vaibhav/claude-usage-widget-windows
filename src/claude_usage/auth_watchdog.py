"""Autonomous auth self-healing for the usage collector.

The widget reads a token to call the plan-usage API. Two things break it:

  * the rotating OAuth token in ``~/.claude/.credentials.json`` gets invalidated
    when Claude Code refreshes in-memory and stops writing the file back, and
  * its refresh token can be revoked (``invalid_grant``), which no script can
    regenerate without a one-time browser sign-in.

The durable cure is a **long-lived token** (``claude setup-token``) which does
not rotate. This watchdog watches for sustained auth failure and, past a
threshold, runs ``claude setup-token`` to (re)mint that long-lived token —
cooldown-limited so it can't spam the browser — capturing the printed token to
``~/.claude/claude-usage-token`` (which :func:`collector._load_long_lived_token`
already prefers). It also surfaces a notification so the user knows.

It deliberately distinguishes *auth* failures (expired/invalid/credentials)
from merely-transient ones (rate-limit / network): only auth failures count
toward triggering a repair, since re-minting a token won't fix a 429.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

from claude_usage.logging_setup import get_logger

_log = get_logger()

# Tunables (overridable via env for power users / tests).
_FAIL_THRESHOLD = int(os.environ.get("CLAUDE_USAGE_AUTH_FAIL_THRESHOLD", "5"))
_REPAIR_COOLDOWN_S = int(os.environ.get("CLAUDE_USAGE_AUTH_REPAIR_COOLDOWN", str(15 * 60)))
_SETUP_TIMEOUT_S = int(os.environ.get("CLAUDE_USAGE_SETUP_TIMEOUT", "180"))

# Error substrings that mean "the token is bad" (vs transient 429/network).
_AUTH_ERROR_MARKERS = (
    "expired",
    "credentials",
    "invalid",
    "no credentials",
    "re-authenticate",
    "unauthor",
)

_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")


def is_auth_error(error: str | None) -> bool:
    """True if *error* indicates a credential problem (not a transient 429/net)."""
    if not error:
        return False
    low = error.lower()
    return any(marker in low for marker in _AUTH_ERROR_MARKERS)


def _find_claude_cli() -> str | None:
    """Locate the claude CLI (PATH, then the standard ~/.local/bin install)."""
    found = shutil.which("claude")
    if found:
        return found
    candidate = os.path.expanduser("~/.local/bin/claude.exe")
    if os.path.isfile(candidate):
        return candidate
    candidate = os.path.expanduser("~/.local/bin/claude")
    return candidate if os.path.isfile(candidate) else None


class AuthWatchdog:
    """Tracks auth-failure streaks and triggers a cooldown-limited token repair."""

    def __init__(
        self,
        claude_dir: str,
        on_repair_attempt=None,
        fail_threshold: int = _FAIL_THRESHOLD,
        cooldown_s: int = _REPAIR_COOLDOWN_S,
    ) -> None:
        self._claude_dir = claude_dir
        self._token_path = os.path.join(claude_dir, "claude-usage-token")
        self._on_repair_attempt = on_repair_attempt  # callback(message:str) for UI/notify
        self._fail_threshold = max(1, fail_threshold)
        self._cooldown_s = cooldown_s
        self._consecutive_auth_fails = 0
        self._last_repair_ts: float | None = None  # None = never repaired yet

    # -- called by the collector after each fetch ---------------------------
    def note_result(self, error: str | None, *, now: float | None = None) -> bool:
        """Record one fetch outcome; auto-repair if the auth-failure streak trips.

        Returns True iff a repair was attempted this call.
        """
        now = time.time() if now is None else now
        if error is None:
            self._consecutive_auth_fails = 0
            return False
        if not is_auth_error(error):
            return False  # transient (429/network) — don't count toward repair

        self._consecutive_auth_fails += 1
        if self._consecutive_auth_fails < self._fail_threshold:
            return False
        if self._last_repair_ts is not None and now - self._last_repair_ts < self._cooldown_s:
            return False  # within cooldown after a prior repair — wait before retrying

        self._last_repair_ts = now
        return self._attempt_repair()

    # -- the actual repair --------------------------------------------------
    def _attempt_repair(self) -> bool:
        cli = _find_claude_cli()
        if cli is None:
            _log.warning("auth-watchdog: claude CLI not found; cannot self-repair")
            self._notify("Claude Usage: auth broken and claude CLI not found — run `claude setup-token` manually.")
            return False

        _log.warning(
            "auth-watchdog: %d consecutive auth failures — running `claude setup-token` to re-mint a long-lived token",
            self._consecutive_auth_fails,
        )
        self._notify("Claude Usage: reconnecting… (a browser may briefly open to approve)")

        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            res = subprocess.run(
                [cli, "setup-token"],
                capture_output=True, text=True,
                timeout=_SETUP_TIMEOUT_S, creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            _log.warning("auth-watchdog: setup-token timed out (browser approval not completed in time)")
            self._notify("Claude Usage: reconnect needs a browser sign-in — run `claude setup-token`.")
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("auth-watchdog: setup-token failed to launch: %s", exc)
            return True

        token = self._extract_token(res.stdout) or self._extract_token(res.stderr)
        if token:
            if self._write_token(token):
                self._consecutive_auth_fails = 0
                _log.info("auth-watchdog: long-lived token re-minted and saved — auth self-healed")
                self._notify("Claude Usage: reconnected ✓")
            return True

        _log.warning("auth-watchdog: setup-token produced no token (likely awaiting browser sign-in)")
        self._notify("Claude Usage: reconnect needs a one-time browser sign-in — run `claude setup-token`.")
        return True

    @staticmethod
    def _extract_token(text: str | None) -> str | None:
        if not text:
            return None
        m = _TOKEN_RE.search(text)
        return m.group(0) if m else None

    def _write_token(self, token: str) -> bool:
        try:
            os.makedirs(self._claude_dir, exist_ok=True)
            tmp = self._token_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(token)
            os.replace(tmp, self._token_path)
            return True
        except OSError as exc:
            _log.warning("auth-watchdog: could not write long-lived token: %s", exc)
            return False

    def _notify(self, message: str) -> None:
        if self._on_repair_attempt is not None:
            try:
                self._on_repair_attempt(message)
            except Exception:  # never let a UI callback break the collector
                pass
