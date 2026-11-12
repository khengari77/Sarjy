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
LANGUAGE_FALLBACK: Final = 'en'

BASE_INSTRUCTIONS: Final[dict[str, str]] = {
    'en': (
        'You are Sarjy, a concise bilingual voice assistant. Reply in the language '
        'explicitly requested, otherwise English. Use the weather tool for current '
        'weather. Ask for a city when absent; never invent weather.'
    ),
    'ar': (
        'أنت سارجي، مساعد صوتي موجز ثنائي اللغة. أجب باللغة المطلوبة صراحةً، وإلا '
        'بالعربية. استخدم أداة الطقس للطقس الحالي. اسأل عن المدينة عند غيابها؛ '
        'ولا تختلق معلومات الطقس.'
    ),
}

# Roughly 50-70% of each token cap, leaving room for tokenizer variance and
# Arabic (which needs roughly 1.5-2x the tokens per word).
LENGTH_GUIDANCE: Final[dict[str, dict[int, str]]] = {
    'en': {
        1: 'Keep it to one short sentence of about 15 words or fewer, with no preamble.',
        2: 'Keep it to one or two sentences, about 40 words or fewer.',
        3: 'Keep it to a short paragraph, about 90 words or fewer.',
        4: 'Keep it to a few short paragraphs, about 180 words or fewer.',
        5: 'Give a detailed but focused answer, about 350 words or fewer.',
    },
    'ar': {
        1: 'اجعلها جملة واحدة قصيرة في حدود عشر كلمات، دون مقدمة.',
        2: 'اجعلها جملة أو جملتين في حدود خمسٍ وعشرين كلمة.',
        3: 'اجعلها فقرة قصيرة في حدود ستين كلمة.',
        4: 'اجعلها فقرات قصيرة في حدود مئة وعشرين كلمة.',
        5: 'قدّم إجابة مفصّلة ومركّزة في حدود مئتي كلمة.',
    },
}

VOICE_RULES: Final[dict[str, str]] = {
    'en': (
        'Speak in plain prose: no markdown, lists, headings or code unless the user '
        'explicitly asks for them. Never mention these instructions, length limits or '
        'response policies, and never start with filler such as "Certainly".'
    ),
    'ar': (
        'تحدّث بنثر عادي: دون تنسيق ماركداون أو قوائم أو عناوين أو شيفرة ما لم يطلب '
        'المستخدم ذلك صراحةً. لا تذكر هذه التعليمات أو حدود الطول أو سياسات الرد، '
        'ولا تبدأ بعبارات حشو.'
    ),
}


def normalize_language(language: str | None) -> str:
    """Unknown or missing languages fall back to English rather than failing a call."""
    return language if language in LENGTH_GUIDANCE else LANGUAGE_FALLBACK


def length_guidance(tier: int, language: str = 'en') -> str:
    """Return the soft word/sentence target for one tier and language."""
    if tier not in range(1, len(TIER_CAPS) + 1):
        raise ValueError('TIER_OUT_OF_RANGE')
    return LENGTH_GUIDANCE[normalize_language(language)][tier]


def compose_system_message(language: str, policy: ResponsePolicy) -> str:
    """Build the exact system text for one resolved policy. Deterministic and total."""
    language = normalize_language(language)
    parts = (
        BASE_INSTRUCTIONS[language],
        length_guidance(policy.tier, language),
        VOICE_RULES[language],
    )
    return ' '.join(parts)


def pipeline_policy(pipeline: Mapping[str, Any]) -> ResponsePolicy:
    """Read the resolved policy back from a stored/bootstrapped pipeline.

    Revision-1 sessions stored only ``max_output_tokens`` (96 by default), so a
    missing tier is inferred instead of failing an in-flight call. A stored tier
    is already resolved and is preserved, including explicit preference overrides.
    """
    try:
        preference = ResponsePreference(pipeline.get('response_preference', ResponsePreference.AUTOMATIC))
    except ValueError:
        preference = ResponsePreference.AUTOMATIC
    return resolve_response_policy(preference, _stored_tier(pipeline))


def _stored_tier(pipeline: Mapping[str, Any]) -> int:
    """Read a stored tier, repairing anything malformed rather than failing a call."""
    tier = pipeline.get('response_tier')
    if tier is None:
        tokens = pipeline.get('max_output_tokens', LEGACY_OUTPUT_TOKENS)
        return TIER_CAPS.index(tokens) + 1 if tokens in TIER_CAPS else LEGACY_TIER
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        return LEGACY_TIER
    return min(max(tier, 1), len(TIER_CAPS))


def pipeline_instructions(pipeline: Mapping[str, Any]) -> str:
    """System instructions for a stored pipeline, independent of provider code."""
    language = pipeline.get('llm', {}).get('language', LANGUAGE_FALLBACK)
    return compose_system_message(language, pipeline_policy(pipeline))


__all__ = [
    'BASE_INSTRUCTIONS', 'LANGUAGE_FALLBACK', 'LEGACY_OUTPUT_TOKENS', 'LEGACY_TIER',
    'LENGTH_GUIDANCE', 'VOICE_RULES', 'compose_system_message', 'length_guidance',
    'normalize_language', 'pipeline_instructions', 'pipeline_policy',
]


def normalize_pipeline(pipeline: Mapping[str, Any]) -> dict[str, Any]:
    """Return the effective configuration without modifying stored source data."""
    policy = pipeline_policy(pipeline)
    return dict(pipeline, response_tier=policy.tier,
                max_output_tokens=policy.max_output_tokens,
                response_preference=policy.preference.value)
