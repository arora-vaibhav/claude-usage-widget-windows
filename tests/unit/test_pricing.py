from claude_usage.pricing import calculate_cost


def _output_rate(model: str) -> float:
    # Cost of exactly 1,000,000 output tokens == the model's output $/M rate.
    return calculate_cost(model, input_tokens=0, output_tokens=1_000_000)["output"]


def test_opus_4_8_priced_as_opus_not_sonnet():
    # Regression: claude-opus-4-8 was missing from the table, so it fell back
    # to Sonnet ($15/M) and undercounted real Opus cost. Opus 4.x tier = $25/M.
    assert _output_rate("claude-opus-4-8") == 25.0
    assert _output_rate("claude-opus-4-8") > _output_rate("claude-sonnet-4-6")


def test_known_tiers_unchanged():
    assert _output_rate("claude-opus-4-7") == 25.0
    assert _output_rate("claude-sonnet-4-6") == 15.0
    assert _output_rate("claude-haiku-4-5-20251001") == 5.0


def test_unknown_opus_release_infers_opus_tier():
    # A future Opus point release must not silently get Sonnet pricing.
    assert _output_rate("claude-opus-5-0") == 25.0


def test_unknown_sonnet_release_infers_sonnet_tier():
    assert _output_rate("claude-sonnet-9-9") == 15.0


def test_unknown_haiku_release_infers_haiku_tier():
    assert _output_rate("claude-haiku-9-9") == 5.0


def test_truly_unknown_family_falls_back_to_sonnet():
    # Non-Claude / unrecognised family keeps the safe Sonnet fallback.
    assert _output_rate("gpt-4o") == 15.0
