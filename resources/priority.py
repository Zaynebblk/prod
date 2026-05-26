PRIORITY_LEVELS = ["high", "medium", "low", "too low"]


def normalize_priority(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("too low", "too_low", "too-low", "toolow"):
        return "too low"
    if text in ("high", "medium", "low"):
        return text
    return text


def quadrant_from_flags(is_urgent, is_important):
    try:
        urg = int(is_urgent or 0)
        imp = int(is_important or 0)
    except Exception:
        urg = 0
        imp = 0
    if urg == 1 and imp == 1:
        return "high"
    if urg == 0 and imp == 1:
        return "medium"
    if urg == 1 and imp == 0:
        return "low"
    return "too low"


def priority_to_quadrant(priority):
    prio = normalize_priority(priority)
    if prio == "high":
        return 1, 1, "Important + Urgent"
    if prio == "medium":
        return 0, 1, "Important, Not Urgent"
    if prio == "low":
        return 1, 0, "Not Important, Urgent"
    return 0, 0, "Not Important, Not Urgent"


def priority_weight(priority):
    prio = normalize_priority(priority)
    if prio == "high":
        return 5
    if prio == "medium":
        return 3
    if prio == "low":
        return 1
    return 0


def priority_session_label(priority):
    prio = normalize_priority(priority)
    if prio == "high":
        return "Deep Focus Session"
    if prio == "medium":
        return "Scheduled Session"
    if prio == "low":
        return "Light Session"
    if prio == "too low":
        return "Avoid / Eliminate"
    return "Session"


def priority_display(priority):
    prio = normalize_priority(priority)
    if not prio:
        return "-"
    return prio.title()
