"""Pure admission policy; database transactions serialize its inputs."""

ACTIVE_CALL_STATUSES: tuple[str, ...] = ("starting", "active")


def call_is_live(status: str, expires_at: str, now: str, *, statuses: tuple[str, ...] = ACTIVE_CALL_STATUSES) -> bool:
    """True while a voice session may still write for its conversation."""
    return status in statuses and expires_at > now


def admission_error(active_owner_ids: tuple[str, ...], owner_id: str) -> str | None:
    if owner_id in active_owner_ids or len(active_owner_ids) >= 2:
        return "CALL_LIMIT_REACHED"
    return None
