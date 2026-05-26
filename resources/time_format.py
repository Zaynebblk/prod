def format_duration_minutes(minutes):
    minutes = int(round(minutes or 0))
    if minutes <= 0:
        return "0 min"

    day_minutes = 60 * 24
    days, remainder = divmod(minutes, day_minutes)
    if days > 0:
        hours, mins = divmod(remainder, 60)
        if hours > 0:
            return f"{days}d {hours}h"
        if mins > 0:
            return f"{days}d {mins}m"
        return f"{days}d"

    hours, mins = divmod(minutes, 60)
    if hours > 0:
        if mins > 0:
            return f"{hours}h {mins}m"
        return f"{hours}h"

    return f"{minutes} min"
