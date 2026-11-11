import hashlib
import json
from dataclasses import dataclass

@dataclass(frozen=True)
class ContextRecord:
    id: str
    role: str
    content: str

def compile_context(records: tuple[ContextRecord, ...], revision: int, excluded_ids: frozenset[str] = frozenset(), max_chars: int = 16000):
    selected = [r for r in records if r.id not in excluded_ids or r.role == "system"]
    protected = sum(len(r.content) for r in selected if r.role == 'system')
    remaining = max(0, max_chars - protected)
    retained = []
    for record in reversed(selected):
        if record.role == 'system':
            retained.append(record)
        elif len(record.content) <= remaining:
            retained.append(record)
            remaining -= len(record.content)
    selected = list(reversed(retained))
    messages = [{"role": r.role, "content": r.content} for r in selected]
    canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"messages": messages, "revision": revision, "included_record_ids": [r.id for r in selected], "canonical_prompt_sha256": hashlib.sha256(canonical.encode()).hexdigest(), "provider_request_capture": "canonical_pre_adapter"}
