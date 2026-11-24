"""Account voice discovery and approval. Credentials and raw provider metadata stay server-side."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from app.domain.deepgram_voices import DEEPGRAM_VOICES, deepgram_voice
from app.domain.voices import parse_voice_ids


class VoiceOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    languages: tuple[str, ...] = ()
    # Optional descriptive traits read from the provider's ``labels`` when they
    # are plain strings. Unknown stays None; no trait is ever inferred.
    gender: str | None = None
    accent: str | None = None
    age: str | None = None
    description: str | None = None


class VoiceCatalog(BaseModel):
    voices: tuple[VoiceOption, ...]
    next_page_token: str | None = None


class VoiceCatalogError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code = code
        self.status = status


def approved_voice_ids(configured: str, default_voice_id: str = "") -> frozenset[str]:
    """The server-curated allow-list. A configured default is approved by definition."""

    approved = set(parse_voice_ids(configured))
    if default_voice_id:
        approved.add(default_voice_id)
    return frozenset(approved)


def resolve_voice(requested: str | None, saved: str | None, default: str) -> tuple[str, str]:
    """Pick the call voice and record why it was chosen.

    Precedence is explicit request, then the saved server setting, then the
    configured default. Only the server setting and default are automatic, so a
    browser never needs a local voice choice to start a call.
    """

    if requested:
        return requested, "requested"
    if saved:
        return saved, "saved"
    if default:
        return default, "default"
    raise VoiceCatalogError("VOICE_DEFAULT_UNCONFIGURED", 503)


def require_approved(voice_id: str, approved: frozenset[str]) -> None:
    if voice_id not in approved:
        raise VoiceCatalogError("VOICE_NOT_APPROVED", 422)


def saved_voice_outcome(code: str) -> Literal["removed", "transient"]:
    """Classify a saved-voice catalog failure. A transient failure is not terminal.

    Only ``VOICE_NOT_APPROVED`` and ``VOICE_UNAVAILABLE`` prove the voice is
    unusable. Everything else (catalog outage, access denied, provider missing)
    says nothing about the voice, so the preference must survive it.
    """

    return "removed" if code in {"VOICE_NOT_APPROVED", "VOICE_UNAVAILABLE"} else "transient"


def spoken_voice_language(source: str | None, requested: str, option: VoiceOption, default_language: str) -> str:
    """Pick the TTS language belonging to the resolved voice.

    The configured default speaks its configured language. An explicit request
    names its own language. A saved preference stores only the voice id, so its
    language comes from the catalog; when the catalog is silent or ambiguous the
    configured default language wins, never the request language, because the
    saved voice is a server-owned choice and the request may be for another
    language entirely.
    """

    if source == "default":
        return default_language
    if source == "saved":
        if requested in option.languages:
            return requested
        if len(option.languages) == 1:
            return option.languages[0]
        return default_language
    return requested


class VoiceProvider(Protocol):
    """The catalogue surface the API consumes from a voice provider."""

    async def list(
        self, search: str = "", next_page_token: str | None = None, voice_id: str | None = None
    ) -> VoiceCatalog: ...

    async def require(self, voice_id: str) -> VoiceOption: ...

    async def find_by_name(self, name: str) -> VoiceOption | None: ...

    async def find_by_trait(self, trait: str, value: str, approved: frozenset[str]) -> VoiceOption | None: ...


def _deepgram_option(voice) -> VoiceOption:
    return VoiceOption(
        id=voice.id,
        name=voice.name,
        languages=("en",),
        gender=voice.gender,
        accent=voice.accent,
        age=voice.age,
        description=voice.description,
    )


class DeepgramVoices:
    """Static, verified Aura-2 English catalogue. Makes no network call."""

    _options = tuple(_deepgram_option(voice) for voice in DEEPGRAM_VOICES)

    async def list(
        self, search: str = "", next_page_token: str | None = None, voice_id: str | None = None
    ) -> VoiceCatalog:
        wanted = " ".join(search.casefold().split())
        voices = tuple(
            option
            for option in self._options
            if (voice_id is None or option.id == voice_id)
            and (not wanted or wanted in option.name.casefold() or wanted in (option.description or ""))
        )
        return VoiceCatalog(voices=voices)

    async def require(self, voice_id: str) -> VoiceOption:
        if deepgram_voice(voice_id) is None:
            raise VoiceCatalogError("VOICE_UNAVAILABLE", 422)
        return next(option for option in self._options if option.id == voice_id)

    async def find_by_name(self, name: str) -> VoiceOption | None:
        wanted = " ".join(name.casefold().split())
        matches = [option for option in self._options if option.name.casefold() == wanted]
        return matches[0] if len(matches) == 1 else None

    async def find_by_trait(self, trait: str, value: str, approved: frozenset[str]) -> VoiceOption | None:
        for voice_id in sorted(approved):
            option = next((o for o in self._options if o.id == voice_id), None)
            if option is not None and getattr(option, trait, None) == value:
                return option
        return None
