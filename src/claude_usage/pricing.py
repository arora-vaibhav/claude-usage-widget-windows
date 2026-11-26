"""API-equivalent prices per model, and the cost of one call.

Rates are USD per million tokens: input, output, cache read, 5-minute cache
write, 1-hour cache write. Source: Anthropic's published first-party API
prices (claude-api skill model table, cached 2026-11-03, with its model notes
for cache reads, fast mode and the Haiku 5.5 long-prompt card). Writes are
1.25x input for the 5-minute TTL and 2x for 1 hour. Cost is API-equivalent:
what the same tokens would cost on the API, not a subscription bill.

Checked against Claude Code's own per-session cost records: its costUSD
equals these rates times its token counts once cache writes it does not split
by TTL are priced at the 1-hour rate (nearly all Opus 5.5 and Sonnet 5.5
records within 2%).

Model ids are normalised before lookup ("[1m]" and date suffixes dropped). An
id that is not in the table but names a known family is priced at that
family's newest model and marked ``family``; anything else is ``unpriced``
and costs 0 so the UI can flag it instead of guessing.
"""

from __future__ import annotations

import re
from typing import Dict, Mapping, Optional, Tuple

PRICES_AS_OF = "2026-11-03"

Rates = Tuple[float, float, float, float, float]  # input, output, cache read, write 5m, write 1h

PRICES: Dict[str, Rates] = {
    "claude-fable-5-2": (8.0, 40.0, 0.20, 10.0, 16.0),
    "claude-mythos-5-2": (8.0, 40.0, 0.20, 10.0, 16.0),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-mythos-5-1": (10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-fable-5": (10.0, 50.0, 1.00, 12.5, 20.0),
    "claude-mythos-5": (10.0, 50.0, 1.00, 12.5, 20.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0, 8.0),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-7": (5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-6": (5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-5": (5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5, 4.0),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5, 4.0),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25, 2.0),
}

# Haiku 5.5 bills the whole call on a 5x card when the prompt (input plus
# cache reads and writes) is over 100K tokens.
LONG_PROMPT_TOKENS = 100_000
LONG_PROMPT: Dict[str, float] = {"claude-haiku-5-5": 5.0}

# speed == "fast" multiplies every rate of the call.
FAST_MULT: Dict[str, float] = {
    "claude-opus-5-5": 2.0,
    "claude-opus-5": 2.0,
    "claude-opus-4-8": 2.0,
    "claude-fable-5-2": 2.0,
    "claude-mythos-5-2": 2.0,
}

WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches

_FAMILY_NEWEST = (
    ("mythos", "claude-fable-5-2"),
    ("fable", "claude-fable-5-2"),
    ("opus", "claude-opus-5-5"),
    ("sonnet", "claude-sonnet-5-5"),
    ("haiku", "claude-haiku-5-5"),
)
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalize(model: str) -> str:
    """'claude-opus-4-7[1m]' -> 'claude-opus-4-7'; 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    m = (model or "").strip().lower().split("[", 1)[0]
    return _DATE_SUFFIX.sub("", m)


def resolve(model: str) -> Tuple[str, Optional[Rates], str]:
    """Return (canonical id, rates or None, 'exact' | 'family' | 'unpriced')."""
    m = normalize(model)
    if m in PRICES:
        return m, PRICES[m], "exact"
    for fam, newest in _FAMILY_NEWEST:
        if fam in m:
            return m, PRICES[newest], "family"
    return m, None, "unpriced"


def multiplier(model: str, long_prompt: bool, speed: Optional[str] = None) -> float:
    """Rate multiplier for one call: the long-prompt card times the fast-mode premium."""
    m = normalize(model)
    mult = LONG_PROMPT.get(m, 1.0) if long_prompt else 1.0
    if speed == "fast":
        mult *= FAST_MULT.get(m, 1.0)
    return mult


def group_cost(model: str, speed: Optional[str], long_prompt: bool, input_tokens: int, output_tokens: int,
               cache_read: int, write_5m: int, write_1h: int, web_search: int = 0) -> float:
    """Cost of summed tokens of calls sharing model, speed and prompt tier (pricing is linear within one)."""
    _, rates, _ = resolve(model)
    if rates is None:
        return web_search * WEB_SEARCH_USD
    ri, ro, rr, r5, r1 = rates
    k = multiplier(model, long_prompt, speed) / 1e6
    tokens_cost = input_tokens * ri + output_tokens * ro + cache_read * rr + write_5m * r5 + write_1h * r1
    return tokens_cost * k + web_search * WEB_SEARCH_USD


def cost_parts(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    write_5m: int = 0,
    write_1h: int = 0,
    web_search: int = 0,
    speed: Optional[str] = None,
) -> Tuple[float, float, float, float]:
    """USD for (input incl. web search, output, cache read, cache write) of one call."""
    _, rates, _ = resolve(model)
    if rates is None:
        return 0.0, 0.0, 0.0, 0.0
    ri, ro, rr, r5, r1 = rates
    k = multiplier(model, input_tokens + cache_read + write_5m + write_1h > LONG_PROMPT_TOKENS, speed) / 1e6
    return (
        input_tokens * ri * k + web_search * WEB_SEARCH_USD,
        output_tokens * ro * k,
        cache_read * rr * k,
        (write_5m * r5 + write_1h * r1) * k,
    )


def call_cost(model: str, *tokens: int, web_search: int = 0, speed: Optional[str] = None) -> float:
    """Total USD for one call: call_cost(model, input, output, cache_read, write_5m, write_1h)."""
    return sum(cost_parts(model, *tokens, web_search=web_search, speed=speed))


# ---------------------------------------------------------------- old API
# widget.py, ticker.py and daily.py still use these until the integration step
# removes them. They only know one cache-write number, priced at the 5m rate.

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    m: {"input": r[0], "output": r[1], "cache_read": r[2], "cache_creation": r[3]} for m, r in PRICES.items()
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> Dict[str, float]:
    i, o, r, w = cost_parts(
        model, max(int(input_tokens), 0), max(int(output_tokens), 0), max(int(cache_read), 0), max(int(cache_creation), 0)
    )
    _, rates, _ = resolve(model)
    savings = max(int(cache_read), 0) * (rates[0] - rates[2]) / 1e6 if rates else 0.0
    return {"total": i + o + r + w, "input": i, "output": o, "cache_read": r, "cache_creation": w, "cache_savings": savings}


def calculate_stats_cost(by_model: Mapping[str, Mapping[str, int]]) -> Dict[str, object]:
    keys = ("total", "input", "output", "cache_read", "cache_creation", "cache_savings")
    totals = dict.fromkeys(keys, 0.0)
    per_model: Dict[str, Dict[str, float]] = {}
    for model, c in by_model.items():
        b = calculate_cost(
            model,
            int(c.get("input", 0) or 0),
            int(c.get("output", 0) or 0),
            int(c.get("cache_read", 0) or 0),
            int(c.get("cache_creation", 0) or 0),
        )
        per_model[model] = b
        for k in keys:
            totals[k] += b[k]
    result: Dict[str, object] = dict(totals)
    result["by_model"] = per_model
    return result
