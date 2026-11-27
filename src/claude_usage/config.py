"""Configuration: built-in defaults, the old-config migration, and atomic saves.

:func:`load_config` merges an optional user JSON file over ``DEFAULT_CONFIG``
(unknown keys are kept) after migrating keys from older versions.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any

Config = dict[str, Any]

DEFAULT_CONFIG: Config = {
    # Where Claude Code keeps its transcripts and credentials; normpath'd so
    # Windows never gets a mixed-separator "C:\\Users\\x/.claude".
    "claude_dir": os.path.normpath(os.path.expanduser("~/.claude")),
    "refresh_seconds": 30,
    "appearance": "system",       # system, light or dark
    "osd_style": "card",          # pill or card
    "osd_opacity": 1.0,
    "osd_scale": 1.0,
    "osd_always_on_top": True,
    "notifications_enabled": True,
    "notify_thresholds": [0.75, 0.90],  # caution, critical (fractions)
    "app_range": "7d",
}

# Themes from before the native app, by the appearance they looked like.
_DARK_THEMES = {"dracula", "nord", "catppuccin-mocha", "gruvbox-dark", "default",
                "dashboard", "hud", "terminal", "strip"}
_LIGHT_THEMES = {"receipt", "brutalist"}
_DROPPED = ("show_ticker", "osd_view_mode", "webhooks", "daily_message_limit", "daily_token_limit",
            "weekly_message_limit", "weekly_token_limit")
_CHOICES = {"appearance": ("system", "light", "dark"), "osd_style": ("pill", "card")}
_NUMBERS = {"refresh_seconds": int, "osd_opacity": float, "osd_scale": float, "osd_x": int, "osd_y": int}
_FLAGS = ("osd_always_on_top", "notifications_enabled")
UNREAD = "_unread"  # in memory only: the file exists but could not be read at start


def migrate(user: Config) -> Config:
    """Map keys of older configs onto the current ones (a copy; *user* is untouched)."""
    user = dict(user)
    theme = user.pop("theme", None)
    if isinstance(theme, str) and "appearance" not in user:
        user["appearance"] = "dark" if theme in _DARK_THEMES else "light" if theme in _LIGHT_THEMES else "system"
    minimized = user.pop("osd_minimized", None)
    if minimized is not None and "osd_style" not in user:
        user["osd_style"] = "pill" if minimized else "card"
    for key in list(user):
        if key in _DROPPED or key.startswith("api_server_"):
            del user[key]
    return user


def drop_bad_values(user: Config) -> Config:
    """Drop values that do not parse (a hand edit, or a bad value from the settings), so the
    defaults apply instead of a crash at startup or a refresh that fails on every tick."""
    for key, kind in _NUMBERS.items():
        if key in user:
            try:
                user[key] = kind(user[key])
                if not math.isfinite(user[key]):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                del user[key]
    if "notify_thresholds" in user:
        try:
            caution, critical = (float(t) for t in user["notify_thresholds"])
            if not 0 < caution < critical <= 1:  # also rejects NaN
                raise ValueError
            user["notify_thresholds"] = [caution, critical]
        except (TypeError, ValueError):
            del user["notify_thresholds"]
    for key in _FLAGS:
        if key in user and not isinstance(user[key], bool):  # the string "false" would read as on
            del user[key]
    for key, ok in _CHOICES.items():
        if key in user and user[key] not in ok:
            del user[key]
    if "claude_dir" in user and not (isinstance(user["claude_dir"], str) and user["claude_dir"]):
        del user["claude_dir"]
    return user


def _read(path: str) -> object:
    for attempt in range(3):
        try:
            with open(path, encoding="utf-8-sig") as f:  # -sig: PowerShell 5 and Notepad can write a BOM
                return json.load(f)
        except OSError:  # an antivirus scan or a sync tool can hold the file for a moment
            if attempt == 2:
                raise
            time.sleep(0.2)


def load_config(path: str) -> Config:
    """Defaults, overlaid with the migrated user file at *path* when it exists and parses."""
    cfg: Config = dict(DEFAULT_CONFIG)
    if not os.path.isfile(path):
        return cfg
    try:
        user_cfg = _read(path)
        if not isinstance(user_cfg, dict):
            raise ValueError(f"must be a JSON object, got {type(user_cfg).__name__}")
    except OSError as exc:
        # Unreadable is not corrupt: the file stays, the app starts on defaults, and
        # save_config merges the file back in before it writes.
        print(f"WARNING: Failed to read config {path}: {exc}", file=sys.stderr)
        cfg[UNREAD] = True
        return cfg
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        # Moved aside, or the first settings save would overwrite what the user had;
        # an earlier .bad copy is never overwritten.
        bad = path + ".bad" if not os.path.exists(path + ".bad") else f"{path}.{int(time.time())}.bad"
        moved = ""
        try:
            os.replace(path, bad)
            moved = f"; kept it as {bad}"
        except OSError:
            pass
        print(f"WARNING: Failed to load config {path}: {exc}{moved}", file=sys.stderr)
        return cfg
    cfg.update(drop_bad_values(migrate(user_cfg)))
    cfg["claude_dir"] = os.path.normpath(os.path.expanduser(str(cfg["claude_dir"])))
    return cfg


def user_config_path() -> str:
    """``$XDG_CONFIG_HOME/claude-usage/config.json``, else ``~/.config/claude-usage/config.json``."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "claude-usage", "config.json")


def save_config(path: str, cfg: Config) -> None:
    """Write the values of *cfg* that differ from the defaults, atomically (temp file,
    then replace), so a crash never leaves half a file.

    Defaults stay out of the file so a changed default, or the claude_dir of a moved
    profile, still applies later. A value JSON cannot hold raises before any write.
    """
    changed = {k: v for k, v in cfg.items() if k != UNREAD and (k not in DEFAULT_CONFIG or DEFAULT_CONFIG[k] != v)}
    if cfg.get(UNREAD) and os.path.isfile(path):
        # The session started on defaults, so its values alone would wipe the user's settings.
        # Still unreadable: OSError, and the caller retries later.
        # shortcut: a value set back to its default this session keeps the file's value; track set keys if needed.
        user = _read(path)
        if isinstance(user, dict):
            changed = {**migrate(user), **changed}
    text = json.dumps(changed, indent=4, sort_keys=True) + "\n"
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
