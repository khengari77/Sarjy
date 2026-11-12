"""Conservative whole-utterance command parsing: no I/O or effects."""
from app.domain.contracts import (
    Action, AdjustSpeakingSpeed, OpenMemoryExplorer, SetCaptionsVisible,
    ShowConversationHistory,
)


def parse_exact_command(text: str) -> Action | None:
    normalized = ' '.join(text.strip().lower().split()).removesuffix('.')
    commands = {
        'speak slower': AdjustSpeakingSpeed(delta=-0.1),
        'speak faster': AdjustSpeakingSpeed(delta=0.1),
        'hide captions': SetCaptionsVisible(visible=False),
        'show captions': SetCaptionsVisible(visible=True),
        'show my previous conversations': ShowConversationHistory(),
        'open the memory explorer': OpenMemoryExplorer(),
    }
    return commands.get(normalized)
