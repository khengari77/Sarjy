"""Which history turns restate a forgotten fact (#194).

The rule under test is the documented one: a turn restates a fact when it contains at least
``min(len(fact content tokens), 2)`` of the fact's distinctive tokens (lower-cased, at least three characters,
not a stopword).  A fact with no distinctive tokens matches nothing.
"""

import string

from app.domain.restatement import content_tokens, restating_turn_ids
from hypothesis import assume, given
from hypothesis import strategies as st

FACT = "My favorite color is blue"


def test_two_of_three_fact_tokens_block_a_turn_but_one_does_not():
    # "My favorite color is blue" -> {favorite, color, blue}; three tokens means two are required.
    assert content_tokens(FACT) == frozenset({"favorite", "color", "blue"})
    turns = [
        # {love, color, blue}: shares color and blue (2) -> blocked.
        ("love", "I love the color blue"),
        # {favorite, color, blue}: shares all three -> blocked.
        ("verbatim", "my favorite color is blue"),
        # {forget, favorite, color}: shares favorite and color (2) -> blocked.
        ("forget", "Forget my favorite color"),
        # {favorite, food, pizza}: shares only favorite (1) -> allowed.
        ("food", "My favorite food is pizza"),
        # {weather}: shares nothing -> allowed.
        ("weather", "what is the weather"),
    ]

    assert restating_turn_ids(turns, [FACT]) == frozenset({"love", "verbatim", "forget"})


def test_matching_is_case_insensitive():
    assert content_tokens("MY FAVORITE COLOR IS BLUE") == frozenset({"favorite", "color", "blue"})
    assert restating_turn_ids([("upper", "I LOVE THE COLOR BLUE")], [FACT]) == frozenset({"upper"})


def test_a_proper_noun_carries_the_fact_on_its_own():
    # The place IS the fact. Naming it alone (any case) restates it; an unrelated turn, or a different place, does not.
    fact = "I live in Benghazi"
    turns = [
        ("both", "I live in Benghazi"),
        ("city", "Benghazi is home"),
        ("lower", "benghazi is where my family is"),
        ("other", "I live in Tripoli"),
        ("visit", "I visited Tripoli last year"),
    ]

    assert restating_turn_ids(turns, [fact]) == frozenset({"both", "city", "lower"})


def test_names_and_acronyms_count_double_like_numbers():
    cases = [
        ("My dog is called Max", "Max needs a walk", True),
        ("My sister Lina lives in Rome", "Lina called me", True),
        ("I work at Acme as an engineer", "my job at Acme is fine", True),
        ("My name is Sam", "say hi to sam", True),
        ("I live in the UK", "I am in the UK", True),
        ("I love AI", "AI is great", True),
        # A generic shared verb is not enough on its own.
        ("I live in the UK", "I live for music", False),
        ("I love AI", "I love this show", False),
        ("My sister Lina lives in Rome", "I like pizza", False),
    ]
    for fact, turn, blocked in cases:
        assert bool(restating_turn_ids([("t", turn)], [fact])) is blocked, (fact, turn)


def test_a_lowercase_value_with_one_shared_word_is_a_documented_limit():
    # Without a name, number or acronym a one-word echo of a longer fact is not detected (accepted, see the PR).
    assert restating_turn_ids([("t", "pizza tonight?")], ["My favorite food is pizza"]) == frozenset()


def test_a_fact_with_one_distinctive_token_matches_on_that_token():
    # One content token lowers the threshold to min(1, 2) == 1, so the city alone is enough.
    fact = "Benghazi"
    assert content_tokens(fact) == frozenset({"benghazi"})
    turns = [("city", "I visited Benghazi"), ("other", "I live in Tripoli")]

    assert restating_turn_ids(turns, [fact]) == frozenset({"city"})


def test_a_fact_without_distinctive_tokens_never_blocks_a_turn():
    turns = [
        ("it-is", "It is"),
        ("this-is-it", "this is it"),
        ("to-be", "to be or not to be"),
        ("stopwords", "the and for"),
    ]
    for fact in ("It is", ""):
        assert content_tokens(fact) == frozenset()
        assert restating_turn_ids(turns, [fact]) == frozenset()
    # A list containing only empty facts still cannot block every turn.
    assert restating_turn_ids(turns, ["", "It is"]) == frozenset()


def test_arabic_tokens_take_part_in_the_same_overlap_rule():
    fact = "أحب اللون الأزرق"
    assert content_tokens(fact) == frozenset({"أحب", "اللون", "الأزرق"})
    turns = [
        # {اللون, الأزرق, جميل}: shares اللون and الأزرق (2 of 3) -> blocked.
        ("two", "اللون الأزرق جميل"),
        # {أحب, الطعام}: shares only أحب (1) -> allowed.
        ("one", "أحب الطعام"),
    ]

    assert restating_turn_ids(turns, [fact]) == frozenset({"two"})


def test_empty_turns_or_facts_return_an_empty_frozenset():
    assert restating_turn_ids([], []) == frozenset()
    assert restating_turn_ids([("turn", "some text")], []) == frozenset()
    assert restating_turn_ids([], [FACT]) == frozenset()


WORD = st.text(alphabet=string.ascii_lowercase, min_size=3, max_size=8)


@given(st.lists(st.tuples(st.uuids().map(str), st.text()), unique_by=lambda pair: pair[0], max_size=20))
def test_no_facts_never_blocks_a_turn(turns):
    assert restating_turn_ids(turns, []) == frozenset()


@given(WORD, WORD, st.text(alphabet=string.ascii_lowercase + " ", max_size=60))
def test_embedding_the_fact_text_inside_noise_always_blocks(first, second, noise):
    fact = f"{first} {second}"
    assume(len(content_tokens(fact)) >= 2)
    turn = f"{noise} {fact} {noise}"

    assert restating_turn_ids([("turn", turn)], [fact]) == frozenset({"turn"})


@given(
    st.lists(st.tuples(st.uuids().map(str), st.text()), unique_by=lambda pair: pair[0], max_size=15),
    st.lists(st.text(max_size=40), max_size=5),
    st.lists(st.text(max_size=40), max_size=5),
)
def test_adding_facts_only_blocks_more_turns(turns, first, second):
    before = restating_turn_ids(turns, first)
    after = restating_turn_ids(turns, [*first, *second])

    assert before <= after


@given(
    st.lists(st.tuples(st.uuids().map(str), st.text()), unique_by=lambda pair: pair[0], max_size=20),
    st.lists(st.text(max_size=40), max_size=5),
)
def test_result_is_a_subset_of_the_given_turn_ids(turns, facts):
    assert restating_turn_ids(turns, facts) <= {turn_id for turn_id, _ in turns}


STOPWORD_OR_SHORT = st.sampled_from(
    [
        "the",
        "and",
        "is",
        "of",
        "to",
        "a",
        "it",
        "my",
        "we",
        "us",
        "in",
        "on",
        "at",
        "as",
        "an",
        "be",
        "do",
        "go",
        "x",
    ]
)


@given(st.lists(STOPWORD_OR_SHORT, max_size=20), st.lists(st.text(max_size=40), max_size=5))
def test_a_turn_of_only_stopwords_or_short_words_is_never_blocked(words, facts):
    turn = " ".join(words)
    assert content_tokens(turn) == frozenset()

    assert restating_turn_ids([("turn", turn)], facts) == frozenset()


def test_a_number_is_always_distinctive_and_carries_a_fact_on_its_own():
    assert content_tokens("I am 26") == frozenset({"26"})
    fact = "I am 26 years old"
    assert restating_turn_ids([("again", "As I said, I am 26"), ("other", "I am happy")], [fact]) == {"again"}


def test_a_fact_with_a_number_blocks_any_turn_repeating_that_number():
    assert restating_turn_ids([("t", "my meeting is at 26 minutes past")], ["I am 26 years old"]) == {"t"}
    # A different number, or a single shared word with no number, is not enough.
    assert restating_turn_ids([("t", "I am 27"), ("u", "that was years")], ["I am 26 years old"]) == frozenset()


@given(st.integers(min_value=0, max_value=10**6), st.lists(st.sampled_from(["alpha", "beta", "gamma"]), max_size=3))
def test_embedding_the_number_of_a_numeric_fact_always_blocks(number, words):
    turn = " ".join([*words, str(number), "please"])
    assert restating_turn_ids([("t", turn)], [f"My number is {number}"]) == {"t"}
