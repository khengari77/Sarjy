"""Account voice discovery. Credentials and raw provider metadata stay server-side."""

import httpx
from pydantic import BaseModel, ConfigDict


class VoiceOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    languages: tuple[str, ...] = ()


class VoiceCatalog(BaseModel):
    voices: tuple[VoiceOption, ...]
    next_page_token: str | None = None


class VoiceCatalogError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code = code
        self.status = status


def public_catalog(payload: dict) -> VoiceCatalog:
    """Pure projection: never pass through samples, settings, or account metadata."""
    if not isinstance(payload, dict) or not isinstance(payload.get("voices"), list):
        raise ValueError("INVALID_VOICE_CATALOG")
    voices = []
    for voice in payload.get("voices", []):
        if not isinstance(voice, dict) or not all(isinstance(voice.get(key), str) for key in ("voice_id", "name")):
            raise ValueError("INVALID_VOICE_CATALOG")
        entries = voice.get("verified_languages", [])
        labels = voice.get("labels")
        if labels is None:
            labels = {}
        if (
            not isinstance(entries, list)
            or any(not isinstance(entry, dict) or not isinstance(entry.get("language", ""), str) for entry in entries)
            or not isinstance(labels, dict)
            or not isinstance(labels.get("language", ""), str)
        ):
            raise ValueError("INVALID_VOICE_CATALOG")
        languages = {entry["language"] for entry in voice.get("verified_languages", []) if entry.get("language")}
        label = (voice.get("labels") or {}).get("language")
        if label:
            languages.add(label)
        voices.append(VoiceOption(id=voice["voice_id"], name=voice["name"], languages=tuple(sorted(languages))))
    if not isinstance(payload.get("has_more", False), bool) or (
        payload.get("next_page_token") is not None and not isinstance(payload["next_page_token"], str)
    ):
        raise ValueError("INVALID_VOICE_CATALOG")
    return VoiceCatalog(
        voices=tuple(voices), next_page_token=payload.get("next_page_token") if payload.get("has_more") else None
    )


class ElevenLabsVoices:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.transport = transport

    async def list(
        self, search: str = "", next_page_token: str | None = None, voice_id: str | None = None
    ) -> VoiceCatalog:
        if not self.api_key:
            raise VoiceCatalogError("VOICE_PROVIDER_NOT_CONFIGURED")
        params = {"page_size": "100", "include_total_count": "false", "include_custom_rates": "false"}
        if search:
            params["search"] = search
        if next_page_token:
            params["next_page_token"] = next_page_token
        if voice_id:
            params["voice_ids"] = voice_id
        try:
            async with httpx.AsyncClient(timeout=8, transport=self.transport) as client:
                result = await client.get(
                    "https://api.elevenlabs.io/v2/voices", params=params, headers={"xi-api-key": self.api_key}
                )
                if result.status_code in {401, 403}:
                    raise VoiceCatalogError("VOICE_CATALOG_ACCESS_DENIED")
                result.raise_for_status()
                return public_catalog(result.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise VoiceCatalogError("VOICE_CATALOG_UNAVAILABLE") from exc

    async def require(self, voice_id: str) -> VoiceOption:
        catalog = await self.list(voice_id=voice_id)
        for voice in catalog.voices:
            if voice.id == voice_id:
                return voice
        raise VoiceCatalogError("VOICE_UNAVAILABLE", 422)
