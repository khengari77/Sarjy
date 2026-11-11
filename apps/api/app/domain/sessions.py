"""Pure admission policy; database transactions serialize its inputs."""
def admission_error(active_owner_ids: tuple[str, ...], owner_id: str, month_calls: int, identity_calls: int, month_budget: int = 80, identity_budget: int = 3) -> str | None:
    if owner_id in active_owner_ids or len(active_owner_ids) >= 2:
        return 'CALL_LIMIT_REACHED'
    if month_calls >= month_budget or identity_calls >= identity_budget:
        return 'DEMO_BUDGET_REACHED'
    return None
