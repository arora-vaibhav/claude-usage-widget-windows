"""API-equivalent prices per model, and the cost of one call.

Rates are USD per million tokens: input, output, cache read, 5-minute cache
write, 1-hour cache write. Source: Anthropic's published first-party API
prices (the platform.claude.com Pricing page, fetched 2026-11-26, with its
notes on cache reads, fast mode, US-only inference and the Haiku 5.5
long-prompt card). Writes are 1.25x input for the 5-minute TTL and 2x for 1 hour. Cost is API-equivalent:
what the same tokens would cost on the API, not a subscription bill.

Checked against Claude Code's own cost records: records whose cache writes all
share one TTL match these rates exactly, and mixed records match a per-call
split. One older Claude Code build priced Sonnet 5.5 cache reads at $0.20; the
Pricing page says $0.10.

Model ids are normalised before lookup ("[1m]", date suffixes, the Bedrock
and Vertex id forms and a "-fast" deployment suffix dropped). An id that is not
in the table but names a known family is priced at that family's newest model
and marked ``family``; anything else, and any Claude 3 id, is ``unpriced`` and
costs 0 so the UI can flag it instead of guessing.
"""

from __future__ import annotations

import functools
import re
from typing import Dict, Optional, Tuple

PRICES_AS_OF = "2026-11-26"

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
    "claude-opus-4-1": (15.0, 75.0, 1.50, 18.75, 30.0),  # retired; rates from the Pricing page, 2026-11-26
    "claude-opus-4": (15.0, 75.0, 1.50, 18.75, 30.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5, 4.0),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5, 4.0),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-sonnet-4": (3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25, 2.0),
}

# Haiku 5.5 bills the whole call on a 5x card when the prompt (input plus
# cache reads and writes) is over 100K tokens.
LONG_PROMPT_TOKENS = 100_000
LONG_PROMPT: Dict[str, float] = {"claude-haiku-5-5": 5.0}

# The stored "speed" is the call's price tier: "fast" multiplies every rate,
# and a "+us" suffix marks US-only inference (inference_geo "us"), 1.1x on
# every token category for Claude 4.6 and later.
US_ONLY_MULT = 1.1
FAST_MULT: Dict[str, float] = {
    "claude-opus-5-5": 2.0,
    "claude-opus-5": 2.0,
    "claude-opus-4-8": 2.0,
    "claude-fable-5-2": 2.0,
    "claude-mythos-5-2": 2.0,
}

WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches

# A refusal before any output is billed only in these stop_details categories (refusals page, 2026-11-26).
BILLED_REFUSALS = frozenset({"bio", "frontier_llm", "reasoning_extraction"})

_FAMILY_NEWEST = (
    ("mythos", "claude-fable-5-2"),
    ("fable", "claude-fable-5-2"),
    ("opus", "claude-opus-5-5"),
    ("sonnet", "claude-sonnet-5-5"),
    ("haiku", "claude-haiku-5-5"),
)


_FAST_ID = re.compile(r"-fast(?=(?:[-@]\d{8})?$)")  # a fast-mode deployment name, as on Foundry


def _parse(model: str) -> Tuple[str, bool]:
    m = (model or "").strip().lower().split("[", 1)[0]
    m = re.sub(r"^(?:[a-z]+\.)?anthropic\.", "", m)
    m = re.sub(r"-v\d+(?::\d+)?$", "", m)
    fast = bool(_FAST_ID.search(m))
    return re.sub(r"[-@]\d{8}$", "", _FAST_ID.sub("", m)), fast


def normalize(model: str) -> str:
    """'claude-opus-4-7[1m]' -> 'claude-opus-4-7'; 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'.

    Bedrock ids ('us.anthropic.claude-sonnet-4-5-20250929-v1:0'), Vertex ids
    ('claude-opus-4-5@20251101') and fast deployment names ('claude-opus-5-5-fast')
    reduce to the same canonical id.
    """
    return _parse(model)[0]


def resolve(model: str) -> Tuple[str, Optional[Rates], str]:
    """Return (canonical id, rates or None, 'exact' | 'family' | 'unpriced')."""
    m = normalize(model)
    if m in PRICES:
        return m, PRICES[m], "exact"
    if m.startswith("claude-3"):
        return m, None, "unpriced"  # retired, and several times dearer than today's card of the same family
    for fam, newest in _FAMILY_NEWEST:
        if fam in m:
            return m, PRICES[newest], "family"
    return m, None, "unpriced"


def multiplier(model: str, long_prompt: bool, speed: Optional[str] = None) -> float:
    """Rate multiplier for one call: the long-prompt card times the fast-mode and US-only premiums."""
    m, fast_id = _parse(model)
    mult = LONG_PROMPT.get(m, 1.0) if long_prompt else 1.0
    if fast_id or (speed or "").startswith("fast"):
        mult *= FAST_MULT.get(m, 1.0)
    if (speed or "").endswith("+us"):
        mult *= US_ONLY_MULT
    return mult


@functools.lru_cache(maxsize=None)
def scaled_rates(model: str, speed: Optional[str], long_prompt: bool) -> tuple:
    """(canonical id, 'exact' | 'family' | 'unpriced', then input, output, cache read, write 5m, write 1h in
    USD per MTok with the call's multipliers applied). Cached: snapshots price thousands of rows."""
    cid, rates, priced = resolve(model)
    k = multiplier(model, bool(long_prompt), speed or None)
    return (cid, priced) + tuple(x * k for x in (rates or (0.0,) * 5))


def group_cost(model: str, speed: Optional[str], long_prompt: bool, input_tokens: int, output_tokens: int,
               cache_read: int, write_5m: int, write_1h: int, web_search: int = 0) -> float:
    """Cost of summed tokens of calls sharing model, speed and prompt tier (pricing is linear within one)."""
    _, _, ri, ro, rr, r5, r1 = scaled_rates(model, speed, bool(long_prompt))
    return ((input_tokens * ri + output_tokens * ro + cache_read * rr + write_5m * r5 + write_1h * r1) / 1e6
            + web_search * WEB_SEARCH_USD)


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
    long = input_tokens + cache_read + write_5m + write_1h > LONG_PROMPT_TOKENS
    _, _, ri, ro, rr, r5, r1 = scaled_rates(model, speed, long)
    return (
        input_tokens * ri / 1e6 + web_search * WEB_SEARCH_USD,
        output_tokens * ro / 1e6,
        cache_read * rr / 1e6,
        (write_5m * r5 + write_1h * r1) / 1e6,
    )


def call_cost(model: str, *tokens: int, web_search: int = 0, speed: Optional[str] = None) -> float:
    """Total USD for one call: call_cost(model, input, output, cache_read, write_5m, write_1h)."""
    return sum(cost_parts(model, *tokens, web_search=web_search, speed=speed))
