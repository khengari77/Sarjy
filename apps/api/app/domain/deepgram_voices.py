"""Static Deepgram Aura-2 English voice table.

Transcribed from https://developers.deepgram.com/docs/tts-models ("Aura-2: all
available English voices", read 2026-11-15). The voice id is the Deepgram model
string. Gender is Deepgram's "Expressed Gender" mapped to the voice-command
vocabulary (masculine -> male, feminine -> female). Aura-2 has no Arabic.
"""

from dataclasses import dataclass

DEEPGRAM_TTS_MODEL = "aura-2"
DEFAULT_DEEPGRAM_VOICE_ID = "aura-2-thalia-en"


@dataclass(frozen=True)
class DeepgramVoice:
    id: str
    name: str
    gender: str
    age: str
    accent: str
    locale: str
    description: str


def _voice(name: str, gender: str, age: str, locale: str, accent: str, traits: str) -> DeepgramVoice:
    return DeepgramVoice(
        id=f"aura-2-{name}-en",
        name=name.capitalize(),
        gender={"masculine": "male", "feminine": "female"}[gender],
        age=age.casefold(),
        accent=accent.casefold(),
        locale=locale,
        description=traits.casefold(),
    )


DEEPGRAM_VOICES: tuple[DeepgramVoice, ...] = (
    _voice("amalthea", "feminine", "Young Adult", "en-ph", "Filipino", "Engaging, Natural, Cheerful"),
    _voice("andromeda", "feminine", "Adult", "en-us", "American", "Casual, Expressive, Comfortable"),
    _voice("apollo", "masculine", "Adult", "en-us", "American", "Confident, Comfortable, Casual"),
    _voice("arcas", "masculine", "Adult", "en-us", "American", "Natural, Smooth, Clear, Comfortable"),
    _voice("aries", "masculine", "Adult", "en-us", "American", "Warm, Energetic, Caring"),
    _voice("asteria", "feminine", "Adult", "en-us", "American", "Clear, Confident, Knowledgeable, Energetic"),
    _voice("athena", "feminine", "Mature", "en-us", "American", "Calm, Smooth, Professional"),
    _voice("atlas", "masculine", "Mature", "en-us", "American", "Enthusiastic, Confident, Approachable, Friendly"),
    _voice("aurora", "feminine", "Adult", "en-us", "American", "Cheerful, Expressive, Energetic"),
    _voice("callista", "feminine", "Adult", "en-us", "American", "Clear, Energetic, Professional, Smooth"),
    _voice("cora", "feminine", "Adult", "en-us", "American", "Smooth, Melodic, Caring"),
    _voice("cordelia", "feminine", "Young Adult", "en-us", "American", "Approachable, Warm, Polite"),
    _voice("delia", "feminine", "Young Adult", "en-us", "American", "Casual, Friendly, Cheerful, Breathy"),
    _voice("draco", "masculine", "Adult", "en-gb", "British", "Warm, Approachable, Trustworthy, Baritone"),
    _voice("electra", "feminine", "Adult", "en-us", "American", "Professional, Engaging, Knowledgeable"),
    _voice("harmonia", "feminine", "Adult", "en-us", "American", "Empathetic, Clear, Calm, Confident"),
    _voice("helena", "feminine", "Adult", "en-us", "American", "Caring, Natural, Positive, Friendly, Raspy"),
    _voice("hera", "feminine", "Adult", "en-us", "American", "Smooth, Warm, Professional"),
    _voice("hermes", "masculine", "Adult", "en-us", "American", "Expressive, Engaging, Professional"),
    _voice("hyperion", "masculine", "Adult", "en-au", "Australian", "Caring, Warm, Empathetic"),
    _voice("iris", "feminine", "Young Adult", "en-us", "American", "Cheerful, Positive, Approachable"),
    _voice("janus", "feminine", "Adult", "en-us", "American", "Southern, Smooth, Trustworthy"),
    _voice("juno", "feminine", "Adult", "en-us", "American", "Natural, Engaging, Melodic, Breathy"),
    _voice("jupiter", "masculine", "Adult", "en-us", "American", "Expressive, Knowledgeable, Baritone"),
    _voice("luna", "feminine", "Young Adult", "en-us", "American", "Friendly, Natural, Engaging"),
    _voice("mars", "masculine", "Adult", "en-us", "American", "Smooth, Patient, Trustworthy, Baritone"),
    _voice("minerva", "feminine", "Adult", "en-us", "American", "Positive, Friendly, Natural"),
    _voice("neptune", "masculine", "Adult", "en-us", "American", "Professional, Patient, Polite"),
    _voice("odysseus", "masculine", "Adult", "en-us", "American", "Calm, Smooth, Comfortable, Professional"),
    _voice("ophelia", "feminine", "Adult", "en-us", "American", "Expressive, Enthusiastic, Cheerful"),
    _voice("orion", "masculine", "Adult", "en-us", "American", "Approachable, Comfortable, Calm, Polite"),
    _voice("orpheus", "masculine", "Adult", "en-us", "American", "Professional, Clear, Confident, Trustworthy"),
    _voice("pandora", "feminine", "Adult", "en-gb", "British", "Smooth, Calm, Melodic, Breathy"),
    _voice("phoebe", "feminine", "Adult", "en-us", "American", "Energetic, Warm, Casual"),
    _voice("pluto", "masculine", "Adult", "en-us", "American", "Smooth, Calm, Empathetic, Baritone"),
    _voice("saturn", "masculine", "Adult", "en-us", "American", "Knowledgeable, Confident, Baritone"),
    _voice("selene", "feminine", "Adult", "en-us", "American", "Expressive, Engaging, Energetic"),
    _voice("thalia", "feminine", "Adult", "en-us", "American", "Clear, Confident, Energetic, Enthusiastic"),
    _voice("theia", "feminine", "Adult", "en-au", "Australian", "Expressive, Polite, Sincere"),
    _voice("vesta", "feminine", "Adult", "en-us", "American", "Natural, Expressive, Patient, Empathetic"),
    _voice("zeus", "masculine", "Adult", "en-us", "American", "Deep, Trustworthy, Smooth"),
)


def deepgram_voice(voice_id: str) -> DeepgramVoice | None:
    return next((voice for voice in DEEPGRAM_VOICES if voice.id == voice_id), None)
