"""Which history turns restate a fact the user asked Sarjy to forget.

Pure. Hiding a memory removes the stored row, but the same words can still sit in earlier turns of the conversation
("I love the color blue", or Sarjy's own "Hidden 'my favorite color is blue'"). If those turns reach the model, the
fact leaks straight back. A turn restates a fact when it shares enough of the fact's distinctive words; the rule errs
towards blocking, and only inside a conversation where the user asked to forget something.
"""

import re
from collections.abc import Iterable
from functools import lru_cache

_TOKEN = re.compile(r"[0-9a-z؀-ۿ]+")
MIN_TOKEN_CHARS = 2
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
        "if",
        "or",
        "by",
        "he",
        "so",
        "no",
        "up",
        "ok",
        "go",
        "oh",
        "uh",
        "um",
        "ah",
        "hm",
        "does",
        "did",
        # Arabic function words, so two of them alone ("أنا من ...") do not make unrelated turns match.
        "أنا",
        "انا",
        "أنت",
        "انت",
        "هو",
        "هي",
        "نحن",
        "من",
        "في",
        "على",
        "إلى",
        "الى",
        "عن",
        "مع",
        "أن",
        "ان",
        "هذا",
        "هذه",
        "ذلك",
        "التي",
        "الذي",
        "كان",
        "كانت",
        "لا",
        "لم",
        "ما",
        "هل",
        "يا",
        "و",
    ]
)
# Fact tokens that must appear in a turn for it to count as restating the fact.
MATCH_TOKENS = 2
STRONG_WEIGHT = 2


@lru_cache(maxsize=4096)
def content_tokens(text: str) -> frozenset[str]:
    """The distinctive words of ``text``: lower-cased, not a common word, and at least two characters.

    A number of any length is always distinctive ("26" is the whole point of "I am 26 years old").
    """
    return frozenset(
        token
        for token in _TOKEN.findall(text.casefold())
        if token not in _STOPWORDS and (len(token) >= MIN_TOKEN_CHARS or token.isdigit())
    )


_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z\u00c0-\u00de][\w\u00c0-\u024f]{2,}")
_ACRONYM = re.compile(r"\b[A-Z]{2,}\b")


@lru_cache(maxsize=4096)
def strong_tokens(text: str) -> frozenset[str]:
    """Tokens that carry a fact on their own: numbers, and proper nouns (a capitalised word that does not start the
    sentence, such as a name or a place). Case is read from the original text, so ASR that lower-cases a name in the
    *turn* still matches a capitalised name in the *fact*."""
    proper = {match.group(0).casefold() for match in _PROPER.finditer(text)}
    proper |= {match.group(0).casefold() for match in _ACRONYM.finditer(text)}
    return frozenset(token for token in content_tokens(text) if token.isdigit() or token in proper)


def _weight(tokens: Iterable[str], strong: frozenset[str]) -> int:
    """A number or proper noun counts double; any other word counts once."""
    return sum(STRONG_WEIGHT if token in strong or token.isdigit() else 1 for token in tokens)


def restating_turn_ids(turns: Iterable[tuple[str, str]], facts: Iterable[str]) -> frozenset[str]:
    """Ids of ``(id, text)`` turns that restate any of ``facts``.

    A turn restates a fact when the weight of the fact's distinctive words it contains reaches ``min(fact weight, 2)``.
    A word weighs 1; a number, a proper noun (a capitalised word that does not start the sentence) or an acronym weighs
    2, because a name, place, number or acronym is usually the whole fact. So "I live in Benghazi" is restated by
    "Benghazi is home", "I am 26 years old" by "I turned 26", and "I live in the UK" by "I am in the UK", while a longer
    fact made only of ordinary words ("My favorite color is blue") needs two of its words. A fact with no distinctive
    words (an erased, empty one) matches nothing.

    Known limits: a lowercase value shared alone ("pizza tonight?" against "My favorite food is pizza"), a name that
    opens the fact's sentence, and a different spelling or form of the same words ("favourite colour", "twenty-six",
    "NYC" for "New York") are not detected. Sarjy's spoken "Hidden '...'" confirmation quotes the stored text, so it
    matches; the background observer, not a growing word list, is the place to judge paraphrases. The rule errs
    towards blocking, and only inside a conversation where the user asked to forget something.
    """
    wanted = []
    for fact in facts:
        tokens = content_tokens(fact)
        if tokens:
            wanted.append((tokens, strong_tokens(fact)))
    if not wanted:
        return frozenset()
    blocked: set[str] = set()
    for turn_id, text in turns:
        words = content_tokens(text)
        if any(
            _weight(tokens & words, strong) >= min(_weight(tokens, strong), MATCH_TOKENS) for tokens, strong in wanted
        ):
            blocked.add(turn_id)
    return frozenset(blocked)
