import pytest

from claude_usage import pricing
from claude_usage.pricing import calculate_cost, call_cost, cost_parts, resolve

M = 1_000_000


def _output_rate(model: str) -> float:
    # Cost of exactly 1,000,000 output tokens == the model's output $/M rate.
    return calculate_cost(model, input_tokens=0, output_tokens=M)["output"]


# Published first-party rates (USD per MTok): input, output, cache read.
@pytest.mark.parametrize("model,inp,out,read", [
    ("claude-fable-5-2", 8, 40, 0.20),
    ("claude-fable-5-1", 10, 50, 0.25),
    ("claude-fable-5", 10, 50, 1.00),
    ("claude-opus-5-5", 4, 20, 0.20),
    ("claude-opus-5", 5, 25, 0.50),
    ("claude-opus-4-8", 5, 25, 0.50),
    ("claude-opus-4-7", 5, 25, 0.50),
    ("claude-opus-4-6", 5, 25, 0.50),
    ("claude-sonnet-5-5", 2, 10, 0.10),
    ("claude-sonnet-5", 2, 10, 0.20),
    ("claude-sonnet-4-6", 3, 15, 0.30),
    ("claude-haiku-5-5", 0.10, 0.50, 0.01),
    ("claude-haiku-4-5", 1, 5, 0.10),
])
def test_current_rates(model, inp, out, read):
    n = 100_000  # at most 100K prompt tokens keeps Haiku 5.5 on its base card
    assert call_cost(model, n, 0, 0, 0, 0) == pytest.approx(inp * n / M)
    assert call_cost(model, 0, M, 0, 0, 0) == pytest.approx(out)
    assert call_cost(model, 0, 0, n, 0, 0) == pytest.approx(read * n / M)
    # 5-minute writes are 1.25x input, 1-hour writes 2x.
    assert call_cost(model, 0, 0, 0, n, 0) == pytest.approx(1.25 * inp * n / M)
    assert call_cost(model, 0, 0, 0, 0, n) == pytest.approx(2 * inp * n / M)
    assert resolve(model)[2] == "exact"


def test_one_hour_writes_cost_more_than_five_minute_writes():
    assert call_cost("claude-opus-5-5", 0, 0, 0, M, 0) == pytest.approx(5.0)
    assert call_cost("claude-opus-5-5", 0, 0, 0, 0, M) == pytest.approx(8.0)


def test_haiku_5_5_long_prompt_card():
    short = call_cost("claude-haiku-5-5", 100_000, M, 0, 0, 0)
    long = call_cost("claude-haiku-5-5", 100_001, M, 0, 0, 0)
    assert short == pytest.approx(0.01 + 0.50)
    assert long == pytest.approx(5 * (100_001 * 0.10 / M + 0.50))


def test_fast_mode_and_web_search():
    assert call_cost("claude-opus-5-5", 0, M, 0, 0, 0, speed="fast") == pytest.approx(40.0)
    assert call_cost("claude-sonnet-5-5", 0, M, 0, 0, 0, speed="fast") == pytest.approx(10.0)
    assert cost_parts("claude-opus-5-5", 0, 0, 0, 0, 0, web_search=3)[0] == pytest.approx(0.03)


def test_ids_are_normalised():
    assert resolve("claude-opus-4-7[1m]")[:1] == ("claude-opus-4-7",)
    assert resolve("claude-haiku-4-5-20251001") == ("claude-haiku-4-5", pricing.PRICES["claude-haiku-4-5"], "exact")


def test_unknown_release_is_priced_by_family_and_marked():
    cid, rates, priced = resolve("claude-opus-5-9")
    assert (rates, priced) == (pricing.PRICES["claude-opus-5-5"], "family")
    assert _output_rate("claude-sonnet-9-9") == 10.0
    assert _output_rate("claude-haiku-9-9") == 0.5


def test_unknown_model_is_unpriced():
    assert resolve("gpt-4o") == ("gpt-4o", None, "unpriced")
    assert call_cost("gpt-4o", M, M, M, M, M) == 0.0


def test_old_api_still_works():
    assert _output_rate("claude-opus-4-8") == 25.0
    assert _output_rate("claude-haiku-4-5-20251001") == 5.0
    b = calculate_cost("claude-opus-5-5", 0, 0, cache_read=M, cache_creation=M)
    assert b["cache_creation"] == pytest.approx(5.0) and b["cache_savings"] == pytest.approx(3.8)
    assert pricing.MODEL_PRICING["claude-sonnet-4-6"]["input"] == 3.0
