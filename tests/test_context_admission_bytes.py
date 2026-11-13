import pytest
from app.domain.context import ContextCandidate, compile_turn_context
from app.domain.provider_bounds import (
    DEEPSEEK_MAX_REQUEST_BYTES,
    TOOL_SCHEMA_RESERVE_BYTES,
    admission_reserve_bytes,
    provider_request_bytes,
)


def candidate(record_id, text, *, role="user", origin="history"):
    return ContextCandidate(id=record_id, role=role, text=text, origin=origin)


def test_arabic_admission_measures_utf8_bytes_not_characters():
    fitting = candidate("u1", "ا" * 7000)
    compiled = compile_turn_context(
        revision=0,
        instructions="Rules",
        current_turn=fitting,
        max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
        reserve_bytes=0,
    )
    assert provider_request_bytes(compiled.messages) <= DEEPSEEK_MAX_REQUEST_BYTES
    assert (len("ا" * 7000)) < DEEPSEEK_MAX_REQUEST_BYTES  # char count alone would look safe

    oversized = candidate("u2", "ا" * 9000)
    with pytest.raises(ValueError, match="CONTEXT_BUDGET_EXCEEDED"):
        compile_turn_context(
            revision=0,
            instructions="Rules",
            current_turn=oversized,
            max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
            reserve_bytes=0,
        )


def test_reserve_accounts_for_schema_and_one_continuation_round():
    current = candidate("u1", "ا" * 7000)
    compiled = compile_turn_context(
        revision=0,
        instructions="Rules",
        current_turn=current,
        max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
        reserve_bytes=0,
    )
    assert provider_request_bytes(compiled.messages) <= DEEPSEEK_MAX_REQUEST_BYTES
    with pytest.raises(ValueError, match="CONTEXT_BUDGET_EXCEEDED"):
        compile_turn_context(
            revision=0,
            instructions="Rules",
            current_turn=current,
            max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
            reserve_bytes=admission_reserve_bytes(1024),
        )


def test_message_envelopes_count_toward_the_bound():
    with pytest.raises(ValueError, match="CONTEXT_BUDGET_EXCEEDED"):
        compile_turn_context(
            revision=0,
            instructions="i" * 400,
            current_turn=candidate("u1", "x" * 15980),
            max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
            reserve_bytes=0,
        )
    compiled = compile_turn_context(
        revision=0,
        instructions="Rules",
        current_turn=candidate("u1", "x" * 15000),
        max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
        reserve_bytes=0,
    )
    assert provider_request_bytes(compiled.messages) <= DEEPSEEK_MAX_REQUEST_BYTES


def test_current_turn_is_never_silently_dropped_at_the_byte_boundary():
    with pytest.raises(ValueError, match="CONTEXT_BUDGET_EXCEEDED"):
        compile_turn_context(
            revision=0,
            instructions="i" * 16000,
            current_turn=candidate("u1", "h" * 1000),
            max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
            reserve_bytes=0,
        )


def test_oversized_single_entry_keeps_its_typed_error():
    with pytest.raises(ValueError, match="CONTEXT_ENTRY_TOO_LONG"):
        compile_turn_context(revision=0, instructions="i" * 16001)


def test_reserve_is_smaller_than_the_provider_bound():
    reserve = admission_reserve_bytes(1024)
    assert reserve >= TOOL_SCHEMA_RESERVE_BYTES
    assert provider_request_bytes([]) + reserve < DEEPSEEK_MAX_REQUEST_BYTES


def test_admission_reserves_room_for_two_tool_rounds():
    reserve = admission_reserve_bytes(1024)
    for body in ("x" * 9000, "ا" * 4000):
        compiled = compile_turn_context(
            revision=0,
            instructions="Rules",
            current_turn=candidate("u1", body),
            max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
            reserve_bytes=reserve,
        )
        assert provider_request_bytes(compiled.messages) + reserve <= DEEPSEEK_MAX_REQUEST_BYTES

    with pytest.raises(ValueError, match="CONTEXT_BUDGET_EXCEEDED"):
        compile_turn_context(
            revision=0,
            instructions="Rules",
            current_turn=candidate("u2", "x" * 10000),
            max_provider_bytes=DEEPSEEK_MAX_REQUEST_BYTES,
            reserve_bytes=reserve,
        )


def test_optional_context_is_dropped_before_the_current_turn():
    older = candidate("old", "o" * 8000)
    current = candidate("current", "hello")
    compiled = compile_turn_context(
        revision=0,
        instructions="Rules",
        history=(older,),
        current_turn=current,
        max_provider_bytes=provider_request_bytes(
            [{"role": "system", "content": "Rules"}, {"role": "user", "content": "hello"}]
        ),
        reserve_bytes=0,
    )
    assert compiled.included_ids == ("system", "current")
