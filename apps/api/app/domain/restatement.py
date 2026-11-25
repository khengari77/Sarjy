"""Which history turns restate a fact the user asked Sarjy to forget.

Pure. Hiding a memory removes the stored row, but the same words can still sit in earlier turns of the conversation
("I love the colour blue", or Sarjy's own "Hidden 'my favourite colour is blue'"). If those turns reach the model, the
fact leaks straight back. A turn restates a fact when it shares enough of the fact's distinctive words; the rule errs
towards blocking, and only inside a conversation where the user asked to forget something.
"""

import re
from collections.abc import Iterable

_TOKEN = re.compile(r"[0-9a-z؀-ۿ]+")
MIN_TOKEN_CHARS = 3
# Words that carry no fact on their own. Deliberately small: a missing word only makes the match stricter to satisfy
# or looser to trip, never a leak, because the overlap threshold below does the real work.
_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "with",
        "that",
        "this",
        "have",
        "has",
        "had",
        "from",
        "your",
        "you",
        "not",
        "but",
        "can",
        "will",
        "would",
        "should",
        "could",
        "about",
        "into",
        "than",
        "then",
        "them",
        "they",
        "their",
        "there",
        "what",
        "when",
        "where",
        "which",
        "who",
        "how",
        "why",
        "my",
        "mine",
        "our",
        "his",
        "her",
        "its",
        "is",
        "am",
        "i",
        "me",
        "we",
        "us",
        "to",
        "of",
        "in",
        "on",
        "at",
        "as",
        "an",
        "a",
        "it",
        "be",
        "do",
        "does",
        "did",
    ]
)
# Fact tokens that must appear in a turn for it to count as restating the fact.
MATCH_TOKENS = 2
NUMBER_WEIGHT = 2


def content_tokens(text: str) -> frozenset[str]:
    """The distinctive words of ``text``: lower-cased, not a common word, and at least three characters.

    A number of any length is always distinctive ("26" is the whole point of "I am 26 years old").
    """
    return frozenset(
        token
        for token in _TOKEN.findall(text.casefold())
        if token not in _STOPWORDS and (len(token) >= MIN_TOKEN_CHARS or token.isdigit())
    )


def _weight(tokens: Iterable[str]) -> int:
    """A number carries a fact on its own, so it counts double; a word counts once."""
    return sum(NUMBER_WEIGHT if token.isdigit() else 1 for token in tokens)


def restating_turn_ids(turns: Iterable[tuple[str, str]], facts: Iterable[str]) -> frozenset[str]:
    """Ids of ``(id, text)`` turns that restate any of ``facts``.

    A turn restates a fact when the weight of the fact's distinctive words it contains reaches ``min(fact weight, 2)``
    (a word weighs 1, a number 2). So a one-word fact ("Benghazi") matches any turn naming it, a longer fact needs
    two of its words, and a fact holding a number ("I am 26 years old") matches any turn repeating the number. A
    fact with no distinctive words (an erased, empty one) matches nothing.
    """
    wanted = [tokens for tokens in (content_tokens(fact) for fact in facts) if tokens]
    if not wanted:
        return frozenset()
    blocked: set[str] = set()
    for turn_id, text in turns:
        words = content_tokens(text)
        if any(_weight(tokens & words) >= min(_weight(tokens), MATCH_TOKENS) for tokens in wanted):
            blocked.add(turn_id)
    return frozenset(blocked)
