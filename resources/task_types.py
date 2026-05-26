TASK_TYPES = [
    "Deep Work",
    "Quick Task",
    "Meeting",
    "Routine",
    "Goal-related",
    "Learning",
]

UNCATEGORIZED_LABEL = "Uncategorized"

TASK_TYPE_COLORS = {
    "Deep Work": "#2563EB",
    "Quick Task": "#22C55E",
    "Meeting": "#F97316",
    "Routine": "#14B8A6",
    "Goal-related": "#A855F7",
    "Learning": "#EAB308",
}

TASK_TYPE_LIMITS = {
    "Deep Work": (25, 50),
    "Learning": (25, 50),
    "Quick Task": (10, 20),
    "Meeting": (60, 120),
}

_TYPE_KEYWORDS = {
    "Meeting": ["call", "meeting", "zoom", "discuss", "discussion", "sync", "standup", "1:1"],
    "Learning": ["study", "learn", "course", "read", "reading", "tutorial"],
    "Deep Work": ["fix", "bug", "code", "coding", "implement", "develop", "debug", "refactor", "problem", "solve", "design"],
    "Routine": ["clean", "organize", "update", "routine", "daily", "habit", "weekly", "review"],
    "Quick Task": ["quick", "send", "reply", "check", "email", "follow up", "small", "minor"],
    "Goal-related": ["plan", "goal", "objective", "roadmap", "milestone", "strategy"],
}


def normalize_task_type(value):
    if not value:
        return None
    val = str(value).strip().lower()
    for t in TASK_TYPES:
        if t.lower() == val:
            return t
    return None


def suggest_task_type(title, description="", important=False):
    text = f"{title or ''} {description or ''}".lower()
    for task_type, keywords in _TYPE_KEYWORDS.items():
        if task_type == "Goal-related" and not important:
            continue
        for word in keywords:
            if word in text:
                return task_type
    return None
