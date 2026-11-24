import pytest
from endpointing import endpointing_kwargs

OFF = {"preemptive_generation": {"enabled": False}}


def test_unset_variables_keep_sdk_endpointing_and_disable_preemptive_generation():
    assert endpointing_kwargs({}) == {"turn_handling": OFF}
    unset = {"ENDPOINTING_MIN_DELAY_SECONDS": "", "ENDPOINTING_MAX_DELAY_SECONDS": ""}
    assert endpointing_kwargs(unset) == {"turn_handling": OFF}


def test_configured_delays_become_turn_handling_options():
    assert endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "0.3", "ENDPOINTING_MAX_DELAY_SECONDS": "2.0"}) == {
        "turn_handling": {**OFF, "endpointing": {"min_delay": 0.3, "max_delay": 2.0}}
    }


@pytest.mark.parametrize(
    "values",
    [{}, {"ENDPOINTING_MIN_DELAY_SECONDS": "0.3", "ENDPOINTING_MAX_DELAY_SECONDS": "1.2"}],
)
def test_the_sdk_session_never_generates_preemptively(values):
    """Regression: once turn_handling is passed the SDK ignores the legacy preemptive_generation=
    keyword, which split every utterance into two user turns in production (2026-11-24)."""
    import asyncio

    from livekit.agents import AgentSession

    async def build():
        return AgentSession(**endpointing_kwargs(values))  # the SDK reads the running loop

    session = asyncio.run(build())
    assert session.options.turn_handling["preemptive_generation"]["enabled"] is False
    if values:
        assert session.options.turn_handling["endpointing"]["min_delay"] == 0.3
        assert session.options.turn_handling["endpointing"]["max_delay"] == 1.2


@pytest.mark.parametrize("values", [{"ENDPOINTING_MAX_DELAY_SECONDS": "2.0"}, {"ENDPOINTING_MIN_DELAY_SECONDS": "0.3"}])
def test_half_a_pair_is_refused(values):
    # The SDK default for the other half depends on the turn detector, so it cannot be checked.
    with pytest.raises(ValueError, match="Set both"):
        endpointing_kwargs(values)


def test_minimum_above_maximum_fails_startup():
    with pytest.raises(ValueError, match="must not exceed"):
        endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "2.0", "ENDPOINTING_MAX_DELAY_SECONDS": "1.0"})


@pytest.mark.parametrize(
    "values",
    [
        {"ENDPOINTING_MIN_DELAY_SECONDS": "0.01", "ENDPOINTING_MAX_DELAY_SECONDS": "2.0"},
        {"ENDPOINTING_MIN_DELAY_SECONDS": "0.3", "ENDPOINTING_MAX_DELAY_SECONDS": "6.1"},
        {"ENDPOINTING_MIN_DELAY_SECONDS": "7.0", "ENDPOINTING_MAX_DELAY_SECONDS": "8.0"},
    ],
)
def test_delays_outside_the_allowed_range_are_refused(values):
    with pytest.raises(ValueError, match="must be between"):
        endpointing_kwargs(values)


def test_non_numeric_delays_are_refused():
    with pytest.raises(ValueError, match="must be a number"):
        endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "fast", "ENDPOINTING_MAX_DELAY_SECONDS": "2.0"})
