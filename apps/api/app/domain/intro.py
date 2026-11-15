"""Deterministic spoken greeting for voice calls. Pure; no model call is ever involved.

The first call of an identity gets the full introduction; later calls get the one-line
greeting. Edit the wording here only. Keep INTRO under 500 characters: the worker charges
it to the speech budget in a single reservation.
"""

from typing import Final, Literal, TypedDict

INTRO: Final[dict[str, str]] = {
    "en": (
        "Hey, I'm Sarjy, your voice buddy. Ask me about the weather, play a quick round of Wordle, "
        "or tell me stuff to remember. I only keep it if you say so, and you can tell me to forget "
        "it any time. Want it dark, or me talking slower? Just say it. So, what's up?"
    ),
    "ar": (
        "مرحبًا، أنا سارجي، مساعدك الصوتي. اسألني عن الطقس، أو العب وردل اليوم، أو أخبرني بأشياء "
        "لأتذكرها. أنت من يقرر ما أحتفظ به: لا أحفظ شيئًا إلا بعد أن تقول نعم، ويمكنك أن تقول "
        "«انسَ ذلك» في أي وقت. جرّب «الوضع الداكن» أو «تكلّم أبطأ». ماذا تريد أن تفعل؟"
    ),
}
SHORT_GREETING: Final[dict[str, str]] = {
    "en": "Hi again, what can I help with?",
    "ar": "أهلًا بك من جديد، بماذا أستطيع مساعدتك؟",
}


class Intro(TypedDict):
    kind: Literal["full", "short"]
    text: str


def intro_for(language: str, *, seen: bool, tts_provider: str | None) -> Intro | None:
    """The greeting to speak for this call, or None when it cannot be spoken.

    Aura-2 (Deepgram) has no Arabic voice, so Arabic sessions on it get no greeting.
    """
    if language not in INTRO or (tts_provider == "deepgram_direct" and language != "en"):
        return None
    if seen:
        return {"kind": "short", "text": SHORT_GREETING[language]}
    return {"kind": "full", "text": INTRO[language]}
