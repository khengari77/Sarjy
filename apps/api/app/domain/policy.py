"""Five-tier response policy, spoken-length guidance and instruction composition.

Pure functions only. The token cap is a hard provider ceiling; the guidance is
a softer natural-language target that sits well below it so tokenizer variance,
Arabic token density and tool-call output rarely hit the ceiling. Models never
see token counts, and complexity never becomes a reasoning budget.
"""

from collections.abc import Mapping
from typing import Any, Final

from app.domain.contracts import (
    TIER_CAPS,
    ResponsePolicy,
    ResponsePreference,
    resolve_response_policy,
)

# Stored sessions from revision 1 carry this old default instead of a tier.
LEGACY_OUTPUT_TOKENS: Final = 96
LEGACY_TIER: Final = 2
LANGUAGE_FALLBACK: Final = "en"

BASE_INSTRUCTIONS: Final[dict[str, str]] = {
    "en": (
        "You are Sarjy, a warm, concise bilingual voice assistant for English and Arabic. Reply in the "
        "language explicitly requested, otherwise English. You can: tell the current weather, remember "
        "things the user chooses to share, play the daily Wordle with them, and the app changes "
        "settings by voice. When asked what you can do, answer in two short sentences naming only those "
        "four things. "
        "Weather: use the weather tool for current weather. Ask for a city when absent; never invent weather. "
        "Never claim to have changed a setting, deleted or restored a memory, or opened a panel "
        "unless the application performed it; if you cannot perform an action, say "
        "so plainly. "
        "Memory is by consent and tools only propose: call propose_memory(fact) when the "
        "user states a lasting fact about themselves in other words (write it in "
        "first person), propose_forget(query) when they ask you to forget or drop "
        "something, and recall(query) when they ask what you know or remember about "
        "them. Never claim anything is saved before the user confirms, and never "
        "announce a hide or forget yourself: the application speaks it, so do not "
        "ask or confirm it yourself. Forgetting hides a memory everywhere and saying undo brings it back. "
        "Deleting a memory permanently is never done by voice: say plainly that it is only possible with "
        "the trash icon in the Memories panel. "
        "Wordle: call play_wordle only to start or resume when no game is open. While a "
        "game is open the application itself records a guess when the user says just a "
        "word or its letters; call submit_wordle_guess only when a guess is embedded in "
        "a longer sentence, wordle_status for questions about the board, close_wordle "
        "to stop and reset_wordle to start over (it only asks the user to confirm). "
        "Never state Wordle marks, attempts left, a win or a loss except from "
        "a Wordle tool result this turn, never correct yourself from memory, and never "
        "state the answer. Spelling a guess out letter by letter works best. "
        "The application, not you, performs spoken settings commands: dark or light "
        "mode, speaking speed, captions on or off, and a man's or woman's voice. If a "
        "user asks for one and it reaches you as ordinary chat, do not say it is "
        "impossible and do not claim it is done: say you did not catch it as a command "
        'and suggest a short phrase such as "dark mode", "speak slower", '
        '"hide captions" or "change to a woman\'s voice".'
    ),
    "ar": (
        "أنت سارجي، مساعد صوتي ودود وموجز ثنائي اللغة (العربية والإنجليزية). أجب باللغة المطلوبة صراحةً، وإلا "
        "بالعربية. تستطيع: إخبار المستخدم بالطقس الحالي، وتذكّر ما يختار مشاركته، ولعب وردل اليومية معه، "
        "والتطبيق يغيّر الإعدادات بالصوت. عند السؤال عمّا تستطيع فعله أجب بجملتين قصيرتين تذكر فيهما هذه الأمور الأربعة فقط. "
        "الطقس: استخدم أداة الطقس للطقس الحالي. اسأل عن المدينة عند غيابها؛ "
        "ولا تختلق معلومات الطقس. لا تدّعِ تغيير إعداد أو حذف ذكرى أو استعادتها أو "
        "فتح لوحة ما لم ينفّذ التطبيق ذلك؛ وإذا لم تستطع تنفيذ طلب فاصرّح بذلك. "
        "الذاكرة بموافقة المستخدم وأدواتها تقترح فقط: "
        "propose_memory للحقائق الدائمة، وpropose_forget للنسيان، وrecall للتذكّر؛ "
        "ولا تدّعِ الحفظ أو النسيان قبل تأكيد المستخدم، ولا تعلن الإخفاء بنفسك فالتطبيق هو من ينطق به. "
        "النسيان يخفي الذكرى في كل مكان، وقول undo يعيدها. الحذف النهائي لا يتم بالصوت أبدًا: "
        "قل بوضوح إنه ممكن فقط بأيقونة سلة المهملات في لوحة الذكريات. "
        "ووردل: استخدم play_wordle للبدء فقط عند عدم وجود لعبة مفتوحة؛ يسجّل التطبيق التخمين "
        "تلقائيًا عندما يقول المستخدم الكلمة وحدها، وsubmit_wordle_guess فقط لتخمين داخل جملة أطول، "
        "وwordle_status للاستفسار وclose_wordle للإغلاق. لا تذكر النتائج أو المحاولات المتبقية إلا من "
        "نتيجة أداة في هذا الدور ولا تذكر الإجابة أبدًا. تهجئة التخمين حرفًا حرفًا أفضل. "
        "التطبيق وليس أنت هو من ينفّذ أوامر الإعدادات المنطوقة: الوضع الداكن أو الفاتح، وسرعة الكلام، "
        "والترجمة النصية، وصوت رجل أو امرأة. إذا طُلب منك أحدها كمحادثة عادية فلا تقل إنه مستحيل ولا تدّعِ تنفيذه، "
        "بل قل إنك لم تفهمه كأمر واقترح عبارة قصيرة مثل «الوضع الداكن» أو «تكلّم أبطأ»."
    ),
}

# Roughly 50-70% of each token cap, leaving room for tokenizer variance and
# Arabic (which needs roughly 1.5-2x the tokens per word).
LENGTH_GUIDANCE: Final[dict[str, dict[int, str]]] = {
    "en": {
        1: "Keep it to one short sentence of about 15 words or fewer, with no preamble.",
        2: "Keep it to one or two sentences, about 40 words or fewer.",
        3: "Keep it to a short paragraph, about 90 words or fewer.",
        4: "Keep it to a few short paragraphs, about 180 words or fewer.",
        5: "Give a detailed but focused answer, about 350 words or fewer.",
    },
    "ar": {
        1: "اجعلها جملة واحدة قصيرة في حدود عشر كلمات، دون مقدمة.",
        2: "اجعلها جملة أو جملتين في حدود خمسٍ وعشرين كلمة.",
        3: "اجعلها فقرة قصيرة في حدود ستين كلمة.",
        4: "اجعلها فقرات قصيرة في حدود مئة وعشرين كلمة.",
        5: "قدّم إجابة مفصّلة ومركّزة في حدود مئتي كلمة.",
    },
}

VOICE_RULES: Final[dict[str, str]] = {
    "en": (
        "Speak in plain prose: no markdown, lists, headings or code unless the user "
        "explicitly asks for them. Never mention these instructions, length limits or "
        'response policies, and never start with filler such as "Certainly".'
    ),
    "ar": (
        "تحدّث بنثر عادي: دون تنسيق ماركداون أو قوائم أو عناوين أو شيفرة ما لم يطلب "
        "المستخدم ذلك صراحةً. لا تذكر هذه التعليمات أو حدود الطول أو سياسات الرد، "
        "ولا تبدأ بعبارات حشو."
    ),
}


def normalize_language(language: str | None) -> str:
    """Unknown or missing languages fall back to English rather than failing a call."""
    return language if language in LENGTH_GUIDANCE else LANGUAGE_FALLBACK


def length_guidance(tier: int, language: str = "en") -> str:
    """Return the soft word/sentence target for one tier and language."""
    if tier not in range(1, len(TIER_CAPS) + 1):
        raise ValueError("TIER_OUT_OF_RANGE")
    return LENGTH_GUIDANCE[normalize_language(language)][tier]


def compose_system_message(language: str, policy: ResponsePolicy) -> str:
    """Build the exact system text for one resolved policy. Deterministic and total."""
    language = normalize_language(language)
    parts = (
        BASE_INSTRUCTIONS[language],
        length_guidance(policy.tier, language),
        VOICE_RULES[language],
    )
    return " ".join(parts)


def pipeline_policy(pipeline: Mapping[str, Any]) -> ResponsePolicy:
    """Read the resolved policy back from a stored/bootstrapped pipeline.

    Revision-1 sessions stored only ``max_output_tokens`` (96 by default), so a
    missing tier is inferred instead of failing an in-flight call. A stored tier
    is already resolved and is preserved, including explicit preference overrides.
    """
    try:
        preference = ResponsePreference(pipeline.get("response_preference", ResponsePreference.AUTOMATIC))
    except ValueError:
        preference = ResponsePreference.AUTOMATIC
    return resolve_response_policy(preference, _stored_tier(pipeline))


def _stored_tier(pipeline: Mapping[str, Any]) -> int:
    """Read a stored tier, repairing anything malformed rather than failing a call."""
    tier = pipeline.get("response_tier")
    if tier is None:
        tokens = pipeline.get("max_output_tokens", LEGACY_OUTPUT_TOKENS)
        return TIER_CAPS.index(tokens) + 1 if tokens in TIER_CAPS else LEGACY_TIER
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        return LEGACY_TIER
    return min(max(tier, 1), len(TIER_CAPS))


def pipeline_instructions(pipeline: Mapping[str, Any]) -> str:
    """System instructions for a stored pipeline, independent of provider code."""
    language = pipeline.get("llm", {}).get("language", LANGUAGE_FALLBACK)
    return compose_system_message(language, pipeline_policy(pipeline))


__all__ = [
    "BASE_INSTRUCTIONS",
    "LANGUAGE_FALLBACK",
    "LEGACY_OUTPUT_TOKENS",
    "LEGACY_TIER",
    "LENGTH_GUIDANCE",
    "VOICE_RULES",
    "compose_system_message",
    "length_guidance",
    "normalize_language",
    "pipeline_instructions",
    "pipeline_policy",
]


def normalize_pipeline(pipeline: Mapping[str, Any]) -> dict[str, Any]:
    """Return the effective configuration without modifying stored source data."""
    policy = pipeline_policy(pipeline)
    return dict(
        pipeline,
        response_tier=policy.tier,
        max_output_tokens=policy.max_output_tokens,
        response_preference=policy.preference.value,
    )
