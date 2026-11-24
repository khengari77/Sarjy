import pytest
from endpointing import endpointing_kwargs


def test_unset_variables_keep_sdk_defaults():
    assert endpointing_kwargs({}) == {}
    assert endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "", "ENDPOINTING_MAX_DELAY_SECONDS": ""}) == {}


def test_configured_delays_become_turn_handling_options():
    assert endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "0.3", "ENDPOINTING_MAX_DELAY_SECONDS": "2.0"}) == {
        "turn_handling": {"endpointing": {"min_delay": 0.3, "max_delay": 2.0}}
    }


def test_one_configured_delay_leaves_the_other_sdk_default():
    assert endpointing_kwargs({"ENDPOINTING_MAX_DELAY_SECONDS": "2.0"}) == {
        "turn_handling": {"endpointing": {"max_delay": 2.0}}
    }
    assert endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "0.3"}) == {
        "turn_handling": {"endpointing": {"min_delay": 0.3}}
    }


def test_minimum_above_maximum_fails_startup():
    with pytest.raises(ValueError, match="must not exceed"):
        endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "2.0", "ENDPOINTING_MAX_DELAY_SECONDS": "1.0"})


@pytest.mark.parametrize(
    "values",
    [
        {"ENDPOINTING_MIN_DELAY_SECONDS": "0.01"},
        {"ENDPOINTING_MAX_DELAY_SECONDS": "6.1"},
        {"ENDPOINTING_MIN_DELAY_SECONDS": "7.0", "ENDPOINTING_MAX_DELAY_SECONDS": "8.0"},
    ],
)
def test_delays_outside_the_allowed_range_fail_startup(values):
    with pytest.raises(ValueError, match="must be between"):
        endpointing_kwargs(values)


def test_non_numeric_delays_fail_startup():
    with pytest.raises(ValueError, match="must be a number"):
        endpointing_kwargs({"ENDPOINTING_MIN_DELAY_SECONDS": "fast"})
