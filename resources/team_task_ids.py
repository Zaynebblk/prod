def _normalize_id(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        text = str(value).strip()
        return text or None


def team_task_key(team_id, task_id):
    team_id = _normalize_id(team_id)
    task_id = _normalize_id(task_id)
    if team_id is None or task_id is None:
        return None
    return f"team:{team_id}:{task_id}"
