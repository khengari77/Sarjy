"""Immutable Day 2 domain contracts.

Pure definitions only: no I/O, no provider clients, no database access. Every
transform returns a new frozen model and never mutates its input.
"""
from enum import StrEnum
from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

EMBEDDING_DIMENSIONS = 1536
EMBEDDING_MODEL = 'text-embedding-3-small'
SPEAKING_SPEED_MIN = 0.8
SPEAKING_SPEED_MAX = 1.2
SPEAKING_SPEED_STEP = 0.1
TIER_CAPS: tuple[int, ...] = (64, 128, 256, 512, 1024)
CONFIDENCE_THRESHOLD = 0.90
CONFIDENCE_LEAD = 0.20


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class Intent(StrEnum):
    CONVERSATION = 'conversation'
    UI_CONTROL = 'ui_control'
    VOICE_SETTING = 'voice_setting'
    MEMORY_SEARCH = 'memory_search'
    MEMORY_MODIFICATION = 'memory_modification'
    CONVERSATION_NAVIGATION = 'conversation_navigation'
    TOOL_REQUEST = 'tool_request'
    AMBIGUOUS = 'ambiguous'


class Complexity(StrEnum):
    SIMPLE = 'simple'
    COMPLEX = 'complex'


class ResponsePreference(StrEnum):
    AUTOMATIC = 'automatic'
    BRIEF = 'brief'
    BALANCED = 'balanced'
    DETAILED = 'detailed'


class DecisionSource(StrEnum):
    LOCAL_EXACT = 'local_exact'
    JEV = 'jev'
    BYPASS = 'bypass'


PREFERENCE_MAX_TIER: dict[ResponsePreference, int] = {
    ResponsePreference.AUTOMATIC: 5,
    ResponsePreference.BRIEF: 2,
    ResponsePreference.BALANCED: 3,
    ResponsePreference.DETAILED: 5,
}


def _on_speed_step(value: float) -> float:
    if abs(round(value * 10) - value * 10) > 1e-9:
        raise ValueError('SPEAKING_SPEED_STEP')
    return value


class ResponsePolicy(Contract):
    """One validated tier. The cap is a ceiling, never a target length."""

    tier: int = Field(ge=1, le=5)
    max_output_tokens: int = Field(ge=TIER_CAPS[0], le=TIER_CAPS[-1])
    preference: ResponsePreference = ResponsePreference.AUTOMATIC

    @model_validator(mode='after')
    def cap_matches_tier(self):
        if self.max_output_tokens != TIER_CAPS[self.tier - 1]:
            raise ValueError('TIER_CAP_MISMATCH')
        return self

    @classmethod
    def for_tier(cls, tier: int, preference: ResponsePreference = ResponsePreference.AUTOMATIC) -> 'ResponsePolicy':
        # Field validation rejects an out-of-range tier before the cap is read.
        safe = min(max(tier, 1), len(TIER_CAPS))
        return cls(tier=tier, max_output_tokens=TIER_CAPS[safe - 1], preference=preference)


def resolve_response_policy(
    preference: ResponsePreference = ResponsePreference.AUTOMATIC,
    explicit_tier: int | None = None,
) -> ResponsePolicy:
    """Apply a saved preference; an explicit current-turn tier wins within bounds."""
    limit = PREFERENCE_MAX_TIER[preference]
    if explicit_tier is not None:
        ResponsePolicy.for_tier(explicit_tier)  # reject out-of-range requests before clamping
    tier = 2 if explicit_tier is None else min(max(explicit_tier, 1), limit)
    return ResponsePolicy.for_tier(tier, preference)


class Decision(Contract):
    """Validated routing output. Confidence cannot authorize mutation by itself."""

    turn_id: str = Field(min_length=1, max_length=100)
    intent: Intent
    probabilities: Mapping[Intent, float]
    confidence: float = Field(ge=0, le=1)
    tier: int = Field(ge=1, le=5)
    complexity: Complexity
    source: DecisionSource
    model: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=40)

    @model_validator(mode='after')
    def probabilities_are_consistent(self):
        if set(self.probabilities) != set(Intent):
            raise ValueError('PROBABILITY_OUTCOMES_MISMATCH')
        total = sum(self.probabilities.values())
        if abs(total - 1) > 1e-6:
            raise ValueError('PROBABILITY_SUM')
        if abs(self.probabilities[self.intent] - self.confidence) > 1e-9:
            raise ValueError('CONFIDENCE_MISMATCH')
        return self

    @property
    def runner_up(self) -> float:
        return max((value for intent, value in self.probabilities.items() if intent is not self.intent), default=0.0)

    @property
    def lead(self) -> float:
        return self.confidence - self.runner_up

    def permits_automatic_action(self, *, threshold: float = CONFIDENCE_THRESHOLD, lead: float = CONFIDENCE_LEAD) -> bool:
        return self.intent is not Intent.AMBIGUOUS and self.confidence >= threshold and self.lead >= lead


class UserSettings(Contract):
    speaking_speed: float = Field(default=1.0, ge=SPEAKING_SPEED_MIN, le=SPEAKING_SPEED_MAX)
    captions_visible: bool = True
    approved_voice_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')
    response_preference: ResponsePreference = ResponsePreference.AUTOMATIC
    revision: int = Field(default=0, ge=0)

    _speed_step = field_validator('speaking_speed')(_on_speed_step)


class MemorySource(Contract):
    turn_id: str = Field(min_length=1, max_length=100)
    conversation_id: str = Field(min_length=1, max_length=100)
    created_at: str = Field(min_length=1, max_length=40)


class MemoryRecord(Contract):
    id: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=4000)
    source: MemorySource
    version: int = Field(ge=1)
    status: Literal['active', 'superseded', 'excluded'] = 'active'
    embedding_model: str = EMBEDDING_MODEL
    embedding: tuple[float, ...] | None = None

    @field_validator('embedding')
    def embedding_dimensions(cls, value):
        if value is not None and len(value) != EMBEDDING_DIMENSIONS:
            raise ValueError('EMBEDDING_DIMENSIONS')
        return value


class ContextEntry(Contract):
    id: str = Field(min_length=1, max_length=100)
    role: Literal['system', 'user', 'assistant', 'memory', 'tool']
    text: str = Field(min_length=1, max_length=16000)


class ContextSelection(Contract):
    revision: int = Field(ge=0)
    included: tuple[ContextEntry, ...] = ()
    excluded_ids: frozenset[str] = frozenset()

    @model_validator(mode='after')
    def excluded_ids_are_not_included(self):
        if self.excluded_ids & {entry.id for entry in self.included}:
            raise ValueError('EXCLUDED_RECORD_INCLUDED')
        return self


class ToolSpec(Contract):
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    description: str = Field(default='', max_length=1000)
    parameters: Mapping[str, Any] = Field(default_factory=dict)


class ModelRequest(Contract):
    turn_id: str = Field(min_length=1, max_length=100)
    attempt_id: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=16000)
    selection: ContextSelection
    response_policy: ResponsePolicy
    settings: UserSettings
    memories: tuple[MemoryRecord, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    settings_revision: int = Field(default=0, ge=0)
    language: Literal['ar', 'en'] = 'en'

    @model_validator(mode='after')
    def memories_are_permitted(self):
        excluded = self.selection.excluded_ids
        if any(memory.id in excluded for memory in self.memories):
            raise ValueError('EXCLUDED_MEMORY_IN_REQUEST')
        return self


class AdjustSpeakingSpeed(Contract):
    kind: Literal['AdjustSpeakingSpeed'] = 'AdjustSpeakingSpeed'
    delta: float

    @field_validator('delta')
    def delta_step(cls, value):
        if value == 0:
            raise ValueError('SPEAKING_SPEED_STEP')
        if abs(round(abs(value) * 10) - abs(value) * 10) > 1e-9:
            raise ValueError('SPEAKING_SPEED_STEP')
        return value


class SetSpeakingSpeed(Contract):
    kind: Literal['SetSpeakingSpeed'] = 'SetSpeakingSpeed'
    speed: float = Field(ge=SPEAKING_SPEED_MIN, le=SPEAKING_SPEED_MAX)

    _speed_step = field_validator('speed')(_on_speed_step)


class SelectApprovedVoice(Contract):
    kind: Literal['SelectApprovedVoice'] = 'SelectApprovedVoice'
    voice_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')


class ShowConversationHistory(Contract):
    kind: Literal['ShowConversationHistory'] = 'ShowConversationHistory'


class OpenMemoryExplorer(Contract):
    kind: Literal['OpenMemoryExplorer'] = 'OpenMemoryExplorer'


class SetCaptionsVisible(Contract):
    kind: Literal['SetCaptionsVisible'] = 'SetCaptionsVisible'
    visible: bool


class SearchMemory(Contract):
    kind: Literal['SearchMemory'] = 'SearchMemory'
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=20)


class ProposeContextExclusion(Contract):
    kind: Literal['ProposeContextExclusion'] = 'ProposeContextExclusion'
    query: str = Field(min_length=1, max_length=200)
    keep: tuple[str, ...] = ()


class RestoreContextOperation(Contract):
    kind: Literal['RestoreContextOperation'] = 'RestoreContextOperation'
    operation_id: str = Field(min_length=1, max_length=100)


ACTION_TYPES = (
    AdjustSpeakingSpeed,
    SetSpeakingSpeed,
    SelectApprovedVoice,
    ShowConversationHistory,
    OpenMemoryExplorer,
    SetCaptionsVisible,
    SearchMemory,
    ProposeContextExclusion,
    RestoreContextOperation,
)
Action = Annotated[Union[ACTION_TYPES], Field(discriminator='kind')]
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def parse_action(payload: Mapping[str, Any]) -> Action:
    """Reject unknown actions and invalid arguments before any handler runs."""
    return ACTION_ADAPTER.validate_python(dict(payload))


def apply_settings_action(settings: UserSettings, action: Action) -> UserSettings:
    """Pure presentation transform: returns a new settings revision or the same object."""
    if isinstance(action, (AdjustSpeakingSpeed, SetSpeakingSpeed)):
        speed = settings.speaking_speed + action.delta if isinstance(action, AdjustSpeakingSpeed) else action.speed
        speed = min(max(round(speed * 10) / 10, SPEAKING_SPEED_MIN), SPEAKING_SPEED_MAX)
        if speed == settings.speaking_speed:
            return settings
        return UserSettings(
            speaking_speed=speed,
            captions_visible=settings.captions_visible,
            approved_voice_id=settings.approved_voice_id,
            response_preference=settings.response_preference,
            revision=settings.revision + 1,
        )
    if isinstance(action, SelectApprovedVoice):
        if action.voice_id == settings.approved_voice_id:
            return settings
        return UserSettings(
            speaking_speed=settings.speaking_speed,
            captions_visible=settings.captions_visible,
            approved_voice_id=action.voice_id,
            response_preference=settings.response_preference,
            revision=settings.revision + 1,
        )
    if isinstance(action, SetCaptionsVisible):
        if action.visible == settings.captions_visible:
            return settings
        return UserSettings(
            speaking_speed=settings.speaking_speed,
            captions_visible=action.visible,
            approved_voice_id=settings.approved_voice_id,
            response_preference=settings.response_preference,
            revision=settings.revision + 1,
        )
    return settings


CONTRACT_MODELS = (
    ResponsePolicy,
    Decision,
    UserSettings,
    MemorySource,
    MemoryRecord,
    ContextEntry,
    ContextSelection,
    ToolSpec,
    ModelRequest,
    *ACTION_TYPES,
)


def contract_components() -> dict[str, dict]:
    """JSON Schemas for the generated TypeScript contracts, kept free of provider values."""
    components: dict[str, dict] = {}
    for model in CONTRACT_MODELS:
        schema = model.model_json_schema(ref_template='#/components/schemas/{model}')
        components.update(schema.pop('$defs', {}))
        components[model.__name__] = schema
    components['Action'] = {'oneOf': [{'$ref': f'#/components/schemas/{model.__name__}'} for model in ACTION_TYPES]}
    return components


__all__ = [
    'ACTION_ADAPTER', 'ACTION_TYPES', 'Action', 'AdjustSpeakingSpeed', 'Complexity',
    'CONFIDENCE_LEAD', 'CONFIDENCE_THRESHOLD', 'CONTRACT_MODELS', 'ContextEntry',
    'ContextSelection', 'Contract', 'Decision', 'DecisionSource', 'EMBEDDING_DIMENSIONS',
    'Intent', 'MemoryRecord', 'MemorySource', 'ModelRequest', 'OpenMemoryExplorer',
    'PREFERENCE_MAX_TIER', 'ProposeContextExclusion', 'ResponsePolicy', 'ResponsePreference',
    'RestoreContextOperation', 'SPEAKING_SPEED_MAX', 'SPEAKING_SPEED_MIN', 'SPEAKING_SPEED_STEP',
    'SearchMemory', 'SelectApprovedVoice', 'SetCaptionsVisible', 'SetSpeakingSpeed',
    'ShowConversationHistory', 'TIER_CAPS', 'ToolSpec', 'UserSettings', 'apply_settings_action',
    'contract_components', 'parse_action', 'resolve_response_policy',
]
