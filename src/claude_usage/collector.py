"""Plan limits from Anthropic: Claude Code's credentials, the token refresh and :func:`fetch_rate_limits`."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime
from typing import Any

from claude_usage.logging_setup import get_logger

_log = get_logger()

_LAST_REFRESH_ATTEMPT: float = 0.0
_REFRESH_COOLDOWN_SECS: float = 300.0
_OAUTH_PAUSE_SECS: float = 300.0  # after the usage endpoint fails, with a long-lived token to fall back on
_oauth_paused_until: float = 0.0


def _refresh_access_token_if_needed(creds_path: str) -> None:
    """Refresh the OAuth access token when it is about to expire.

    Uses curl.exe (ships with Windows 10/11) to POST to the OAuth token
    endpoint.  curl TLS fingerprint is accepted by Cloudflare; Python urllib
    and PowerShell Invoke-WebRequest return 429.

    Refresh tokens rotate on every successful use, so both the new
    access_token and the new refresh_token are written back to the credentials file.
    """
    global _LAST_REFRESH_ATTEMPT
    _CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    _TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
    try:
        with open(creds_path, encoding="utf-8-sig") as _f:  # utf-8-sig strips BOM
            _creds = json.load(_f)
        _oauth = _creds.get("claudeAiOauth", {})
        _expires_ms = int(_oauth.get("expiresAt") or 0)
        _now_ms = int(time.time() * 1000)
        if _expires_ms > _now_ms + 5 * 60 * 1000:
            return  # still valid for 5+ minutes
        _now_s = time.time()
        if _now_s - _LAST_REFRESH_ATTEMPT < _REFRESH_COOLDOWN_SECS:
            return  # within cooldown
        _LAST_REFRESH_ATTEMPT = _now_s
        _refresh_tok = _oauth.get("refreshToken", "")
        if not _refresh_tok:
            return
        # curl.exe: Cloudflare accepts its TLS fingerprint (unlike urllib's or PowerShell's)
        import subprocess as _sp
        _curl = r"C:\Windows\System32\curl.exe"
        if not os.path.isfile(_curl):
            _curl = "curl"
        _kw: dict[str, Any] = {}
        if sys.platform == "win32":
            _kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        _res = _sp.run(
            [
                _curl, "-s",
                "-X", "POST", _TOKEN_URL,
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "-H", "User-Agent: claude-code/1.0",
                "-d", "@-",  # the body on stdin: a command line is readable by other local processes
            ],
            input=f"grant_type=refresh_token&refresh_token={_refresh_tok}&client_id={_CLIENT_ID}",
            capture_output=True, text=True, timeout=15, **_kw,
        )
        if _res.returncode != 0:
            _log.warning("token refresh: curl exited %s: %s",
                         _res.returncode, (_res.stderr or "").strip()[:200])
            return
        _tok_data = json.loads(_res.stdout)
        _new_access = _tok_data.get("access_token", "")
        _new_refresh = _tok_data.get("refresh_token", "")
        _exp_in = int(_tok_data.get("expires_in", 28800))
        if not _new_access:
            _log.warning(
                "token refresh rejected by endpoint: %s",
                _tok_data.get("error_description") or _tok_data.get("error")
                or "no access_token in response",
            )
            return
        # Write the credentials back: the refresh token rotates on use
        _creds["claudeAiOauth"]["accessToken"] = _new_access
        if _new_refresh:
            _creds["claudeAiOauth"]["refreshToken"] = _new_refresh
        _creds["claudeAiOauth"]["expiresAt"] = int((time.time() + _exp_in) * 1000)
        _rexp = _tok_data.get("refresh_token_expires_in")
        if _new_refresh and _rexp:  # the old token's expiry would end the new one's life early
            _creds["claudeAiOauth"]["refreshTokenExpiresAt"] = int((time.time() + int(_rexp)) * 1000)
        _tmp = creds_path + ".tmp"
        try:  # owner-only, on disk before the rename: the old refresh token is already dead
            with os.fdopen(os.open(_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as _f:
                json.dump(_creds, _f, separators=(",", ":"))  # compact, as Claude Code writes it
                _f.flush()
                os.fsync(_f.fileno())
            for _left in range(9, -1, -1):
                try:
                    os.replace(_tmp, creds_path)
                    break
                except PermissionError:  # a Claude Code process reading the file holds it for a moment
                    if not _left:
                        raise  # the rotated token is lost: Claude Code asks for a sign-in at its next refresh
                    time.sleep(0.05)
        finally:
            if os.path.exists(_tmp):
                os.remove(_tmp)  # a failed write leaves no copy of the tokens behind
        _LAST_REFRESH_ATTEMPT = 0.0  # reset so next check sees valid token and skips
    except Exception as _exc:
        _log.warning("token refresh failed: %s", _exc)


LONG_LIVED_TOKEN_FILENAME = "claude-usage-token"


def _load_long_lived_token(claude_dir: str) -> str | None:
    """Return a user-provided long-lived token, if one is configured.

    Set up once via ``claude setup-token`` and stored either in the
    ``CLAUDE_USAGE_TOKEN`` environment variable or in
    ``<claude_dir>/claude-usage-token``. Unlike the rotating OAuth access token
    in ``.credentials.json`` (which Claude Code invalidates when it refreshes
    in-memory during active use), a long-lived token does not rotate, so the
    widget keeps working without re-logins.
    """
    env = (os.environ.get("CLAUDE_USAGE_TOKEN") or "").strip()
    if env:
        return env
    path = os.path.join(claude_dir, LONG_LIVED_TOKEN_FILENAME)
    try:
        if os.path.isfile(path):
            tok = open(path, encoding="utf-8-sig").read().strip()
            return tok or None
    except OSError:
        pass
    return None


def _load_credentials(claude_dir: str) -> str | None:
    """Claude Code's OAuth access token, from the credentials file or the macOS Keychain.

    The file (``~/.claude/.credentials.json``, every platform) is refreshed first
    when its token expires within 5 minutes, as v1 did: see
    :func:`_refresh_access_token_if_needed`. The Keychain fallback handles macOS
    installs where Claude Code keeps its credentials there instead.

    Returns the raw access-token string (maybe expired), or ``None`` if there is none.
    """
    # 1. Try the credentials file (Linux + macOS)
    creds_path = os.path.join(claude_dir, ".credentials.json")
    if os.path.isfile(creds_path):
        _refresh_access_token_if_needed(creds_path)
        try:
            with open(creds_path, encoding="utf-8-sig") as f:  # utf-8-sig strips BOM if present
                creds = json.load(f)
            return creds["claudeAiOauth"]["accessToken"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # File exists but is unreadable or lacks the token key; try Keychain

    # 2. Keychain fallback, macOS only; /usr/bin/security is the canonical CLI
    if sys.platform == "darwin":
        try:
            import subprocess

            result = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    "Claude Code-credentials",
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                creds = json.loads(result.stdout.strip())
                return creds["claudeAiOauth"]["accessToken"]
        except Exception:
            pass

    return None


def _fetch_unified_headers_via_messages(token: str) -> dict[str, Any]:
    """Read plan utilization from the ``anthropic-ratelimit-unified-*`` response
    headers of a tiny ``/v1/messages`` call, using curl (Cloudflare rate-limits
    urllib's TLS fingerprint).

    These unified headers carry the SAME 5h/7d plan-level utilization as
    ``/api/oauth/usage``. This path is required for long-lived ``setup-token``
    credentials, which lack the ``user:profile`` scope the OAuth-usage endpoint
    demands (it 403s) but still receive the unified headers here.
    """
    import subprocess as _sp

    _curl = r"C:\Windows\System32\curl.exe"
    if not os.path.isfile(_curl):
        _curl = "curl"
    _kw: dict[str, Any] = {}
    if sys.platform == "win32":
        _kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "h"}],
    })
    try:
        res = _sp.run(
            [
                _curl, "-s", "-D", "-", "-o", os.devnull, "-w", "\n%{http_code}",
                "https://api.anthropic.com/v1/messages",
                "-H", "@-",  # the Authorization header on stdin, never on the command line
                "-H", "anthropic-version: 2023-06-01",
                "-H", "anthropic-beta: oauth-2025-04-20",
                "-H", "content-type: application/json",
                "-d", body,
            ],
            input=f"Authorization: Bearer {token}\n",
            capture_output=True, text=True, timeout=15, **_kw,
        )
    except (OSError, _sp.SubprocessError):
        return {"error": "Can't reach the usage API. Check your network."}
    if res.returncode != 0:
        return {"error": "Can't reach the usage API. Check your network."}
    out = res.stdout or ""
    nl = out.rfind("\n")
    status = out[nl + 1:].strip() if nl >= 0 else ""
    head_text = out[:nl] if nl >= 0 else out
    headers: dict[str, str] = {}
    for line in head_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    if any(k.startswith("anthropic-ratelimit-unified-") for k in headers):
        return _parse_rate_limit_headers(headers)
    if status == "401":
        return {"error": "Credentials expired. Run claude to sign in again."}
    if status == "429":
        return {"error": "Rate limited by the usage API. Retrying shortly."}
    return {"error": f"The usage API returned error {status or '?'}."}


def fetch_rate_limits(claude_dir: str) -> dict[str, Any]:
    """The plan's limits from Anthropic, or ``{"error": sentence}``, plus ``plan`` from the credentials.

    Two credential shapes are supported:

    * **Subscription OAuth** (``.credentials.json``): has the ``user:profile``
      scope, so we use Claude Code's ``/api/oauth/usage`` endpoint directly. With a
      long-lived token it is used only while valid and never refreshed; without one
      it is refreshed shortly before it expires (written back atomically), as in v1.
    * **Long-lived token** (``claude setup-token``): lacks ``user:profile`` and
      403s on that endpoint, so it reads the same 5h/7d plan utilization from
      the ``/v1/messages`` unified rate-limit headers. It is the fallback when the
      OAuth path fails; ``source`` says which one answered.
    """
    return dict(_fetch_limits(claude_dir), plan=_plan(claude_dir))


def _plan(claude_dir: str) -> str | None:
    """'Ultra' (Max 20x until 2026-10-30), 'Max' (5x) or 'Pro' from Claude Code's credentials; None when unknown."""
    try:
        with open(os.path.join(claude_dir, ".credentials.json"), encoding="utf-8-sig") as f:
            oauth = json.load(f)["claudeAiOauth"]
        tier, sub = str(oauth.get("rateLimitTier") or "").lower(), str(oauth.get("subscriptionType") or "").lower()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if "max_20x" in tier or "ultra" in tier or sub == "ultra":
        return "Ultra"
    if "max_5x" in tier:
        return "Max"
    if sub == "pro" or tier.endswith("_pro"):
        return "Pro"
    return None  # a bare "max" was both Max tiers


def _fetch_limits(claude_dir: str) -> dict[str, Any]:
    global _oauth_paused_until
    # Claude Code's own token reads /api/oauth/usage, which carries the scoped limits, the surface
    # breakdown and extra usage. A long-lived token (sk-ant-oat...) 403s there (no user:profile scope)
    # and only reads the unified headers, so it is the fallback: when Claude Code's token is expired
    # and could not be refreshed, or the endpoint failed.
    long_lived = _load_long_lived_token(claude_dir)
    if long_lived:
        # A long-lived token exists so nothing here rotates Claude Code's sign-in (v1 order): a rotation
        # would strand the refresh token a running claude process holds. Its token is read only while valid.
        oauth = None if time.time() < _oauth_paused_until else _read_oauth_token(claude_dir)
    else:
        oauth = _load_credentials(claude_dir)
    if oauth:
        res = _fetch_oauth_usage(oauth)
        if "error" not in res:
            return res
        if long_lived:  # a 429, 401 or 5xx: the headers meanwhile, one call per fetch, not two
            _oauth_paused_until = time.time() + _OAUTH_PAUSE_SECS
        elif "Rate limited" in res["error"]:
            return res  # transient: the worker backs off; the headers call would be rate limited too
    token = long_lived or oauth
    if not token:
        return {"error": "No credentials found. Run claude to sign in."}
    return _fetch_unified_headers_via_messages(token)


def _fetch_oauth_usage(token: str) -> dict[str, Any]:
    """Hit ``/api/oauth/usage`` and translate the response into the same
    shape ``_parse_rate_limit_headers`` produces, so callers don't care
    which path won.

    The response uses 0-100 percentages and ISO-8601 ``resets_at`` strings;
    we normalise both to the internal 0-1 fraction + unix-seconds epoch.
    """
    # Use curl.exe like the token refresh does. Cloudflare rate-limits (429s)
    # Python urllib's TLS fingerprint about 4 times out of 5, but accepts curl's;
    # hitting this endpoint via urllib was the real cause of the frequent
    # "stale" flapping (the 429 was being mis-reported as "Credentials expired").
    import subprocess as _sp

    _curl = r"C:\Windows\System32\curl.exe"
    if not os.path.isfile(_curl):
        _curl = "curl"
    _run_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        _run_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        _res = _sp.run(
            [
                _curl, "-s", "-w", "\n%{http_code}",
                "https://api.anthropic.com/api/oauth/usage",
                "-H", "@-",  # the Authorization header on stdin, never on the command line
                "-H", "anthropic-beta: oauth-2025-04-20",
                "-H", "User-Agent: claude-code/1.0",
            ],
            input=f"Authorization: Bearer {token}\n",
            capture_output=True, text=True, timeout=15, **_run_kwargs,
        )
    except (OSError, _sp.SubprocessError):
        return {"error": "Can't reach the usage API. Check your network."}
    if _res.returncode != 0:
        return {"error": "Can't reach the usage API. Check your network."}
    _out = _res.stdout or ""
    _nl = _out.rfind("\n")
    _status = _out[_nl + 1:].strip() if _nl >= 0 else ""
    _body = _out[:_nl] if _nl >= 0 else _out
    if _status == "401":
        return {"error": "Credentials expired. Run claude to sign in again."}
    if _status == "429":
        return {"error": "Rate limited by the usage API. Retrying shortly."}
    if _status != "200":
        return {"error": f"The usage API returned error {_status or '?'}."}
    try:
        payload = json.loads(_body)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        return {"error": "The usage API sent an unexpected reply."}

    return _parse_oauth_usage(payload)


def _read_oauth_token(claude_dir: str) -> str | None:
    """Claude Code's OAuth access token from the file if it has not expired; this one never refreshes."""
    try:
        with open(os.path.join(claude_dir, ".credentials.json"), encoding="utf-8-sig") as f:
            oauth = json.load(f)["claudeAiOauth"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    expires = oauth.get("expiresAt")
    if isinstance(expires, (int, float)) and expires / 1000 < time.time() + 60:
        return None
    return oauth.get("accessToken") or None


def _frac(v: Any, scale: float = 100.0) -> float | None:
    """A percentage (or a fraction with scale=1) as a 0..1 fraction; None when missing."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v) / scale
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return max(0.0, min(f, 1.0))


def _epoch(v: Any) -> int | None:
    """An ISO 8601 string or epoch number (s or ms) as epoch seconds; None when missing."""
    if isinstance(v, str):
        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
        except ValueError:
            try:
                v = float(v)
            except ValueError:
                return None
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not v:
        return None
    v = int(v)
    return v // 1000 if v > 4_102_444_800 else v


def _parse_oauth_usage(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate an /api/oauth/usage payload. Missing values stay None, never 0."""
    def obj(v: Any) -> dict:
        return v if isinstance(v, dict) else {}

    five = obj(payload.get("five_hour"))
    seven = obj(payload.get("seven_day"))
    extra = payload.get("extra_usage") if isinstance(payload.get("extra_usage"), dict) else None
    raw_limits = [x for x in payload.get("limits") or [] if isinstance(x, dict)]
    shares = {x.get("limit_index"): x for x in payload.get("weekly_scoped_shares") or [] if isinstance(x, dict)}
    limits = []
    for i, x in enumerate(raw_limits):
        share = obj(shares.get(i))
        limits.append({
            "kind": x.get("kind"),
            "group": x.get("group"),
            "name": obj(obj(x.get("scope")).get("model")).get("display_name"),
            "pct": _frac(x.get("percent")),
            "severity": x.get("severity"),
            "resets_at": _epoch(x.get("resets_at")),
            "active": x.get("is_active"),
            "allowance": _frac(share.get("allowance_percent_of_weekly")),
            "used_of_weekly": _frac(share.get("used_percent_of_weekly")),
        })
    weekly_all = next((x for x in limits if x["kind"] == "weekly_all"), {})
    weekly_pct = _frac(seven.get("utilization"))
    if weekly_pct is None:
        weekly_pct = weekly_all.get("pct")
    weekly = None
    if weekly_pct is not None:
        weekly = {"pct": weekly_pct, "resets_at": _epoch(seven.get("resets_at")) or weekly_all.get("resets_at"),
                  "severity": weekly_all.get("severity")}
    five_pct = _frac(five.get("utilization"))
    five_hour = None if five_pct is None else {
        "pct": five_pct, "resets_at": _epoch(five.get("resets_at")), "severity": None}
    bd = obj(payload.get("seven_day_breakdown"))
    surfaces = [{"key": r.get("key"), "name": r.get("display_name") or r.get("key"), "pct": _frac(r.get("percent")),
                 "pct_of_limit": _frac(r.get("percent_of_limit"))}
                for r in bd.get("rows") or [] if isinstance(r, dict)]
    extra_out = None
    if extra is not None:
        dp = extra.get("decimal_places")
        div = 10 ** (dp if isinstance(dp, int) else 2)
        used = obj(obj(payload.get("spend")).get("used"))
        used_val = None
        if isinstance(used.get("amount_minor"), (int, float)):
            used_val = used["amount_minor"] / 10 ** int(used.get("exponent") or 2)
        elif isinstance(extra.get("used_credits"), (int, float)):
            used_val = extra["used_credits"] / div
        limit = extra.get("monthly_limit")
        extra_out = {
            "enabled": bool(extra.get("is_enabled")),
            "monthly_limit": limit / div if isinstance(limit, (int, float)) else None,
            "used": used_val,
            "currency": extra.get("currency") or used.get("currency") or "",
            "reason": extra.get("disabled_reason"),
            "spend_limit_reached": extra.get("spend_limit_reached"),
        }
    return {
        "source": "oauth",
        "fetched_at": int(time.time()),
        "weekly": weekly,
        "five_hour": five_hour,
        "limits": limits,
        "surfaces": surfaces,
        "window_started_at": _epoch(bd.get("window_started_at")),
        "extra_usage": extra_out,
        "status": None,
        "representative_claim": None,
    }


def _parse_rate_limit_headers(headers: dict[str, str]) -> dict[str, Any]:
    """Parse Anthropic unified rate-limit headers; a missing or malformed value stays None."""
    prefix = "anthropic-ratelimit-unified-"
    if not any(k.startswith(prefix) for k in headers):
        return {"error": "The usage API sent no limits."}

    weekly_pct = _frac(headers.get(prefix + "7d-utilization"), 1.0)
    five_pct = _frac(headers.get(prefix + "5h-utilization"), 1.0)
    overage = headers.get(prefix + "overage-status")
    return {
        "source": "headers",
        "fetched_at": int(time.time()),
        "weekly": None if weekly_pct is None else {
            "pct": weekly_pct, "resets_at": _epoch(headers.get(prefix + "7d-reset")), "severity": None},
        "five_hour": None if five_pct is None else {
            "pct": five_pct, "resets_at": _epoch(headers.get(prefix + "5h-reset")), "severity": None},
        "limits": [],
        "surfaces": [],
        "window_started_at": None,
        "extra_usage": None if overage is None else {
            "enabled": overage == "allowed", "monthly_limit": None, "used": None, "currency": "",
            "reason": headers.get(prefix + "overage-disabled-reason"), "spend_limit_reached": None},
        "status": headers.get(prefix + "status"),
        "representative_claim": headers.get(prefix + "representative-claim"),
    }
