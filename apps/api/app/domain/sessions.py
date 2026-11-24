"""Pure admission policy; database transactions serialize its inputs."""


def admission_error(active_owner_ids: tuple[str, ...], owner_id: str) -> str | None:
    if owner_id in active_owner_ids or len(active_owner_ids) >= 2:
        return "CALL_LIMIT_REACHED"
    return None
