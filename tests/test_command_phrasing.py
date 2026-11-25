"""Conversational wrappers and extra phrasings resolve to the same typed actions as the exact rows."""

import asyncio

import pytest
from app.domain.commands import (
    _EXACT_COMMANDS,
    normalize_utterance,
    parse_exact_command,
    parse_memory_exclusion_target,
    parse_voice_gender,
    parse_voice_selection,
    unsupported_command,
)
from app.domain.contracts import AdjustSpeakingSpeed, SetCaptionsVisible, SetTheme
from app.domain.memory_capture import parse_capture
from hypothesis import assume, given, settings
from hypothesis import strategies as st


@pytest.mark.parametrize(
    "text,theme",
    [
        ("Change to light mode.", "light"),
        ("Can you change to light mode?", "light"),
        ("Okay, could you please switch to dark mode?", "dark"),
        ("change it to the dark theme", "dark"),
        ("make it dark", "dark"),
        ("go light", "light"),
        ("Set the theme to dark", "dark"),
        ("turn on dark mode, please", "dark"),
        ("use automatic theme", "system"),
        ("Light mode.", "light"),
    ],
)
def test_theme_phrasings(text, theme):
    assert parse_exact_command(text) == SetTheme(theme=theme)


@pytest.mark.parametrize(
    "text,delta",
    [
        ("Can you speak slower?", -0.1),
        ("could you talk a bit faster please", 0.1),
        ("slow down", -0.1),
        ("Speed up.", 0.1),
        ("Speak more quickly", 0.1),
    ],
)
def test_speed_phrasings(text, delta):
    assert parse_exact_command(text) == AdjustSpeakingSpeed(delta=delta)


@pytest.mark.parametrize(
    "text,visible",
    [
        ("Can you hide the subtitles?", False),
        ("show me the captions", True),
        ("Please turn the captions off", False),
        ("subtitles on", True),
    ],
)
def test_caption_phrasings(text, visible):
    assert parse_exact_command(text) == SetCaptionsVisible(visible=visible)


@pytest.mark.parametrize(
    "text,gender",
    [
        ("Change the voice to a man", "male"),
        ("Can you use a female voice?", "female"),
        ("I want a woman's voice", "female"),
        ("sound like a guy", "male"),
        ("make your voice a lady", "female"),
    ],
)
def test_voice_gender_phrasings(text, gender):
    assert parse_voice_gender(text) == gender


@pytest.mark.parametrize(
    "text",
    [
        "make it lighter",
        "I love dark mode",
        "dark mode is nice",
        "turn off dark mode and hide captions",
        "can you speak",
        "what does light mode do",
        "change to",
        "please",
        '"switch to dark mode"',
        "don't switch to dark mode",
    ],
)
def test_near_misses_are_not_commands(text):
    assert parse_exact_command(text) is None


def test_normalizer_strips_only_the_wrapper():
    assert normalize_utterance("Okay, can you change to light mode, please?") == "change to light mode"
    assert normalize_utterance("Forget My Name.", fold=False) == "Forget My Name"
    assert parse_memory_exclusion_target("Could you forget my name?") == "my name"


@pytest.mark.parametrize(
    "text,content",
    [
        ("Remember, my name is Abdulrahman.", "my name is Abdulrahman."),
        ("Remember: I live in Tripoli", "I live in Tripoli"),
        ("Save this fact. My name is Abdurrahman.", "My name is Abdurrahman."),
        ("Save that: I prefer tea", "I prefer tea"),
        ("Could you remember that my name is Sam?", "my name is Sam"),
        ("Please note that I work at Acme", "I work at Acme"),
        ("Keep in mind that I am vegetarian", "I am vegetarian"),
        ("Don't forget, my birthday is in May", "my birthday is in May"),
    ],
)
def test_explicit_save_phrasings_capture_the_fact(text, content):
    capture = parse_capture(text)
    assert capture is not None and capture.explicit
    assert capture.text.rstrip(".") == content.rstrip(".")


@pytest.mark.parametrize(
    "text",
    [
        "Can you remember my name?",
        "Do you remember my name?",
        "Save this file",
        "Save this",
        "Save this fact",
        "Could you remember it?",
        "Would you remember that if I told you?",
        "note that",
    ],
)
def test_questions_and_incomplete_requests_never_capture(text):
    capture = parse_capture(text)
    assert capture is None or not capture.explicit


@pytest.mark.parametrize(
    "text",
    [
        "You're pushing me to my dark mode",
        "I like dark mode better",
        "My dark mode is when I'm sad",
        "Dark mode is bad for your eyes",
        "Do you have dark mode?",
        "don't switch to dark mode",
        "I said dark mode",
        "Light mode, honestly, is better",
        "Speak faster than me",
        "You speak faster",
        "Why do you speak slower?",
        "Slow down, I'm getting confused about my life",
        "I always turn off captions on Netflix",
        "My ex used to say: use the dark theme",
        "switch to a man",
    ],
)
def test_figurative_and_near_miss_sentences_are_not_commands(text):
    assert parse_exact_command(text) is None
    assert parse_voice_gender(text) is None
    assert parse_voice_selection(text) is None


# Extra words that are neither filler leads nor polite wrappers, so appending
# them must make an exact command stop matching instead of being stripped.
_EXTRA_WORDS = ["banana", "yesterday", "because", "my", "friend", "said", "when", "never"]


@pytest.mark.parametrize("phrase", sorted(_EXACT_COMMANDS))
@settings(max_examples=200)
@given(
    prefix=st.lists(st.sampled_from(_EXTRA_WORDS), max_size=3),
    suffix=st.lists(st.sampled_from(_EXTRA_WORDS), max_size=3),
)
def test_extra_words_never_leave_an_exact_command(phrase, prefix, suffix):
    assume(prefix or suffix)
    assert parse_exact_command(phrase) is not None
    assert parse_exact_command(" ".join([*prefix, phrase, *suffix])) is None


@pytest.mark.parametrize(
    "text",
    [
        "Forget it",
        "Forget about it",
        "Forget you!",
        "Forget the weather, tell me a joke",
        "forget the trip and tell me about Rome",
        "Forget me, I'm leaving",
        "Forget the car rental. I want to keep the hotel booking.",
        "Forget the car rental. I should keep the hotel booking",
    ],
)
def test_forget_idioms_and_clause_breaks_are_not_memory_commands(text):
    assert parse_memory_exclusion_target(text) is None
    assert unsupported_command(text) is None


@pytest.mark.parametrize(
    ("text", "target"),
    [("Forget my favorite color", "my favorite color"), ("forget my favorite show", "my favorite show")],
)
def test_plain_forget_still_names_a_target(text, target):
    assert parse_memory_exclusion_target(text) == target


def test_compound_forget_keeps_its_honest_refusal():
    assert unsupported_command("forget the trip but keep the budget")[0] == "MEMORY_MUTATION_UNSUPPORTED"
    assert unsupported_command("delete my name")[0] == "MEMORY_MUTATION_UNSUPPORTED"


@pytest.mark.parametrize(
    "text",
    [
        "forget the trip KEEP the budget",
        "forget the trip Keep the budget",
        "Forget the trip EXCEPT the hotel",
        "forget the trip but I keep the budget",
        "forget the trip and we keep the budget",
    ],
)
def test_keep_clauses_are_compound_whatever_the_case_or_subject(text):
    assert unsupported_command(text) is not None
    assert parse_memory_exclusion_target(text) is None


@pytest.mark.parametrize(
    ("text", "target"),
    [
        ("forget where I keep my passport", "where I keep my passport"),
        ("forget where they keep the spare key", "where they keep the spare key"),
        ("forget what I keep in my safe", "what I keep in my safe"),
        ("forget my plan to keep the gym membership", "my plan to keep the gym membership"),
        ("forget Dr. Smith's number", "Dr. Smith's number"),
        ("forget the U.S. trip", "the U.S. trip"),
    ],
)
def test_possessive_keep_is_a_fact_not_a_keep_clause(text, target):
    assert unsupported_command(text) is None
    assert parse_memory_exclusion_target(text) == target


@pytest.mark.parametrize(
    "text",
    [
        "all of it",
        "all of that",
        "this chat",
        "the conversation",
        "all of them",
        "everything about us",
        "stuff",
        "it's",
        "what's that",
        "something",
    ],
)
def test_targets_that_name_nothing_never_search(text):
    from app.services.context_operations import has_content_words
    from app.services.voice_exclusions import hide_target

    assert not has_content_words(text)
    # db=None proves the guard returns before any lookup, on the path the model's forget tool also uses.
    result = asyncio.run(hide_target(None, owner_id="o", conversation_id="c", target=text, request_id="r", search=None))
    assert result.detail == "MEMORY_TARGET_UNSPECIFIED"


def test_a_real_memory_word_still_counts_as_content():
    from app.services.context_operations import has_content_words

    assert has_content_words("X")
    assert has_content_words("7")
    assert has_content_words("3.5")
    assert has_content_words("the 7")
    assert has_content_words("Dr. Smith's number")
    assert not has_content_words("a")
    assert not has_content_words("i")
    assert not has_content_words("it's")

    assert has_content_words("my favorite color")
    assert has_content_words("the Paris trip")
